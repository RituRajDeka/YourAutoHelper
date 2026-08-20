"""Download the source video with yt-dlp (the only video-fetch network call).

We use the yt-dlp *Python API* rather than shelling out so failures surface as
exceptions we can translate into clean errors. The server must never crash on a
bad or blocked URL.
"""

from __future__ import annotations

import logging
import os
import re
import uuid
from pathlib import Path
from typing import Callable, Optional

import yt_dlp

from .models import InvalidVideoURLError
from .paths import DOWNLOADS_DIR

logger = logging.getLogger(__name__)

# Strip ANSI colour codes yt-dlp sometimes embeds in its error strings.
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")

# Browsers we'll borrow cookies from (in order) when YouTube throws up a
# sign-in / "confirm you're not a bot" wall. Override with env vars below.
_DEFAULT_BROWSERS = ["chrome", "edge", "brave", "firefox", "opera", "vivaldi"]
_COOKIE_FILE_ENV = "CLIPFORGE_COOKIES_FILE"       # path to a cookies.txt
_COOKIE_BROWSER_ENV = "CLIPFORGE_COOKIES_BROWSER"  # force one browser, e.g. "chrome"


class YouTubeDownloadError(InvalidVideoURLError):
    """Refined download error that stores a classified error code."""
    
    def __init__(self, error_code: str, message: str):
        self.error_code = error_code
        self.message = message
        super().__init__(f"[{error_code}] {message}")


class DownloadConfiguration:
    """Encapsulates the configuration parameters for a download job."""
    
    def __init__(
        self,
        format_spec: str = "bv*[height<=1080]+ba/b[height<=1080]/b",
        merge_output_format: str = "mp4",
        outtmpl: str = "",
        progress_hook: Optional[Callable[[dict], None]] = None
    ):
        self.format_spec = format_spec
        self.merge_output_format = merge_output_format
        self.outtmpl = outtmpl
        self.progress_hook = progress_hook

    def get_base_opts(self) -> dict:
        opts = {
            "format": self.format_spec,
            "merge_output_format": self.merge_output_format,
            "outtmpl": self.outtmpl,
            "noplaylist": True,
            "quiet": True,
            "no_warnings": True,
            "ignoreerrors": False,
        }
        if self.progress_hook is not None:
            opts["progress_hooks"] = [self.progress_hook]
        return opts


class AuthenticationProvider:
    """Manages parsing, validating, and diagnosing authentication cookies/browsers."""
    
    def __init__(self):
        self.cookie_file = os.environ.get(_COOKIE_FILE_ENV)
        self.forced_browser = os.environ.get(_COOKIE_BROWSER_ENV)

    def run_diagnostics(self) -> dict:
        """Run safe diagnostics on the authentication state without exposing secret values."""
        diag = {
            "configured": "no",
            "exists": "no",
            "size": 0,
            "format_valid": "fail",
            "has_auth_cookies": "no"
        }
        
        # Check environment variable
        if self.cookie_file:
            diag["configured"] = "yes"
            cookie_path = Path(self.cookie_file)
            if cookie_path.exists():
                diag["exists"] = "yes"
                size = cookie_path.stat().st_size
                diag["size"] = size
                
                try:
                    with open(cookie_path, "r", encoding="utf-8", errors="ignore") as f:
                        lines = f.readlines()
                    
                    # Validate Netscape format header
                    has_header = any(
                        line.startswith("# Netscape HTTP Cookie File") or 
                        line.startswith("# HTTP Cookie File") 
                        for line in lines
                    )
                    if has_header:
                        diag["format_valid"] = "pass"
                    
                    # Validate presence of expected YouTube authentication keys
                    expected_keys = {
                        "SID", "HSID", "SSID", "APISID", "SAPISID", 
                        "LOGIN_INFO", "__Secure-1PSID", "__Secure-3PSID"
                    }
                    found_keys = set()
                    for line in lines:
                        if not line.strip() or line.startswith("#"):
                            continue
                        parts = line.strip().split("\t")
                        if len(parts) >= 7:
                            name = parts[5]
                            if name in expected_keys:
                                found_keys.add(name)
                    
                    if found_keys:
                        diag["has_auth_cookies"] = f"yes ({len(found_keys)} keys found: {', '.join(sorted(found_keys))})"
                except Exception as e:
                    logger.warning("Error running cookie diagnostics: %s", e)
        elif self.forced_browser:
            diag["configured"] = f"browser ({self.forced_browser})"
            
        return diag

    def get_download_strategies(self, base_opts: dict) -> list[tuple[str, dict]]:
        """Generate different player client and cookie options attempts."""
        run_opts = dict(base_opts)
        if self.cookie_file and Path(self.cookie_file).exists():
            run_opts["cookiefile"] = self.cookie_file

        strategies: list[tuple[str, dict]] = []
        
        # 1. Try standard client (with cookies if loaded)
        strategies.append(("default", dict(run_opts)))

        # 2. Try client variations (with cookies if loaded)
        player_clients = ["android", "ios", "tv", "mweb"]
        for client in player_clients:
            strategies.append((
                f"{client} client",
                {**run_opts, "extractor_args": {"youtube": {"player_client": [client]}}},
            ))

        # 3. Try browser cookies fallback
        if self.forced_browser:
            b = self.forced_browser.strip().lower()
            strategies.append((f"{b} cookies", {**base_opts, "cookiesfrombrowser": (b,)}))
        elif not self.cookie_file:  # Only auto-try local browsers if no cookies file was loaded
            for b in _DEFAULT_BROWSERS:
                strategies.append((f"{b} cookies", {**base_opts, "cookiesfrombrowser": (b,)}))

        return strategies


def _clean_ydl_error(raw: str) -> str:
    """Turn a raw yt-dlp DownloadError string into one short, readable line."""
    text = _ANSI_RE.sub("", raw or "").strip()
    if not text:
        return ""
    # Keep only the first line — that's the human reason.
    line = text.splitlines()[0]
    line = re.sub(r"^ERROR:\s*", "", line).strip()
    # Drop yt-dlp's "; please report this issue …" tail and extractor prefixes.
    line = re.split(r";\s*(please report|you might want)", line, maxsplit=1)[0].strip()
    line = re.sub(r"^\[[^\]]+\]\s*[^:]*:\s*", "", line)  # e.g. "[youtube] ID: "
    return line[:300]


def classify_error(err_str: str, auth_diag: dict) -> tuple[str, str]:
    """Classify the raw yt-dlp error string into a standard category and clean message."""
    err_lower = err_str.lower()
    clean_msg = _clean_ydl_error(err_str)
    
    if "invalid" in err_lower or "unsupported url" in err_lower or "incomplete youtube id" in err_lower:
        return "INVALID_URL", clean_msg
    if "private video" in err_lower or "is private" in err_lower:
        return "PRIVATE_VIDEO", clean_msg
    if "does not exist" in err_lower or "not found" in err_lower or "unavailable" in err_lower or "been removed" in err_lower:
        return "VIDEO_NOT_FOUND", clean_msg
    if "sign in" in err_lower or "log in" in err_lower or "login" in err_lower or "members-only" in err_lower or "age" in err_lower:
        if auth_diag.get("exists") == "no":
            return "COOKIES_MISSING", f"Authentication required but cookies file was not found. Details: {clean_msg}"
        if auth_diag.get("format_valid") == "fail":
            return "COOKIES_INVALID", f"Authentication required but cookie file is malformed or invalid. Details: {clean_msg}"
        return "AUTHENTICATION_REQUIRED", f"Authentication required. Cookies are configured but rejected by YouTube. Details: {clean_msg}"
    if "confirm you're not a bot" in err_lower or "bot" in err_lower or "page needs to be reloaded" in err_lower:
        return "BOT_CHECK", f"YouTube blocked this download with a bot verification check: {clean_msg}"
    if "format" in err_lower or "requested format" in err_lower:
        return "FORMAT_ERROR", clean_msg
    if "http error" in err_lower or "403" in err_lower or "401" in err_lower:
        return "AUTHENTICATION_REQUIRED", clean_msg
    if "connection" in err_lower or "timeout" in err_lower or "timed out" in err_lower or "name or service not known" in err_lower or "network" in err_lower:
        return "NETWORK_ERROR", clean_msg
    
    return "UNKNOWN_DOWNLOAD_ERROR", clean_msg


def download_video(
    url: str, progress_hook: Optional[Callable[[dict], None]] = None
) -> Path:
    """Download `url` to downloads/<uuid>.mp4 and return the file path.

    Args:
        url: source video URL.
        progress_hook: optional yt-dlp progress callback.

    Raises:
        YouTubeDownloadError: on any download failure, with a classified error code.
    """
    if not url or not url.strip():
        raise YouTubeDownloadError("INVALID_URL", "No video URL was provided.")

    clip_uuid = uuid.uuid4().hex
    out_template = str(DOWNLOADS_DIR / f"{clip_uuid}.%(ext)s")
    expected_path = DOWNLOADS_DIR / f"{clip_uuid}.mp4"

    # Configure the download parameters
    config = DownloadConfiguration(outtmpl=out_template, progress_hook=progress_hook)
    base_opts = config.get_base_opts()

    # Authenticate and run diagnostics
    auth_provider = AuthenticationProvider()
    auth_diag = auth_provider.run_diagnostics()

    # Safe diagnostics logging
    logger.info("--- DOWNLOAD DIAGNOSTICS ---")
    logger.info("yt-dlp version: %s", yt_dlp.version.__version__)
    logger.info("Authentication configured: %s", auth_diag["configured"])
    logger.info("Cookie file exists: %s", auth_diag["exists"])
    logger.info("Cookie file size: %s bytes", auth_diag["size"])
    logger.info("Cookie format validation: %s", auth_diag["format_valid"])
    logger.info("YouTube auth cookies: %s", auth_diag["has_auth_cookies"])
    logger.info("----------------------------")

    # Generate attempts
    attempts = auth_provider.get_download_strategies(base_opts)
    
    last_reason = ""
    last_exc: Optional[Exception] = None
    ok = False
    
    for label, opts in attempts:
        # Pacing request: short sleep to avoid triggering anti-bot protection
        import time
        time.sleep(1)

        # Retry strategy for transient network/extractor failures
        max_retries = 2
        for attempt_idx in range(max_retries + 1):
            try:
                logger.info("Attempting download using strategy: %s (try %d/%d)", label, attempt_idx + 1, max_retries + 1)
                with yt_dlp.YoutubeDL(opts) as ydl:
                    ydl.download([url.strip()])
                ok = True
                logger.info("Successfully downloaded %s using %s", url, label)
                break
            except Exception as exc:
                last_reason = str(exc)
                last_exc = exc
                
                # Classify the error
                err_code, clean_msg = classify_error(last_reason, auth_diag)
                logger.warning("yt-dlp [%s] failed: %s (classified: %s)", label, clean_msg, err_code)
                
                # Terminal errors: raise exception immediately
                if err_code in {"INVALID_URL", "VIDEO_NOT_FOUND", "PRIVATE_VIDEO"}:
                    raise YouTubeDownloadError(err_code, clean_msg) from exc
                
                # Authentication/bot block: stop trying this strategy, move to fallback
                if err_code in {"BOT_CHECK", "AUTHENTICATION_REQUIRED", "COOKIES_INVALID"}:
                    break
                    
                # Network errors: retry this strategy
                if err_code == "NETWORK_ERROR" and attempt_idx < max_retries:
                    logger.info("Transient network error detected, retrying strategy: %s...", label)
                    time.sleep(2 * (attempt_idx + 1))
                    continue
                
                # Default fallback
                break
                
        if ok:
            break

    if not ok:
        err_code, clean_msg = classify_error(last_reason, auth_diag)
        raise YouTubeDownloadError(err_code, clean_msg)

    # Some sources may not produce exactly <uuid>.mp4 (e.g. a different
    # container survived the merge). Fall back to any file with our uuid prefix.
    if expected_path.exists():
        return expected_path

    candidates = sorted(DOWNLOADS_DIR.glob(f"{clip_uuid}.*"))
    if candidates:
        return candidates[0]

    raise YouTubeDownloadError(
        "UNKNOWN_DOWNLOAD_ERROR",
        "The download completed but no output file was produced. The video may be unavailable or region-locked."
    )
