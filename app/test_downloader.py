import os
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

from app.downloader import (
    YouTubeDownloadError,
    AuthenticationProvider,
    classify_error,
    download_video
)
from app.models import InvalidVideoURLError

def test_custom_exception_inheritance():
    """Verify that YouTubeDownloadError inherits from InvalidVideoURLError."""
    exc = YouTubeDownloadError("BOT_CHECK", "YouTube blocked this request")
    assert isinstance(exc, InvalidVideoURLError)
    assert exc.error_code == "BOT_CHECK"
    assert exc.message == "YouTube blocked this request"
    assert "[BOT_CHECK]" in str(exc)

def test_classify_error_rules():
    """Test error classification maps raw yt-dlp error strings to correct codes."""
    auth_diag = {"exists": "yes", "format_valid": "pass"}
    
    # Invalid URL
    code, msg = classify_error("ERROR: Incomplete YouTube ID", auth_diag)
    assert code == "INVALID_URL"
    
    # Private Video
    code, msg = classify_error("ERROR: Private video. Sign in if you've been granted access", auth_diag)
    assert code == "PRIVATE_VIDEO"
    
    # Video Not Found
    code, msg = classify_error("ERROR: Video unavailable. This video has been removed", auth_diag)
    assert code == "VIDEO_NOT_FOUND"
    
    # Bot Check
    code, msg = classify_error("ERROR: Sign in to confirm you're not a bot", auth_diag)
    assert code == "BOT_CHECK"
    
    code, msg = classify_error("ERROR: The page needs to be reloaded", auth_diag)
    assert code == "BOT_CHECK"
    
    # Authentication Required
    code, msg = classify_error("ERROR: Sign in to confirm your age", {"exists": "no"})
    assert code == "COOKIES_MISSING"
    
    code, msg = classify_error("ERROR: Sign in to confirm your age", {"exists": "yes", "format_valid": "fail"})
    assert code == "COOKIES_INVALID"
    
    code, msg = classify_error("ERROR: Sign in to confirm your age", {"exists": "yes", "format_valid": "pass"})
    assert code == "AUTHENTICATION_REQUIRED"
    
    # Network Error
    code, msg = classify_error("ERROR: Unable to download webpage: <urlopen error [Errno -2] Name or service not known>", auth_diag)
    assert code == "NETWORK_ERROR"

def test_authentication_provider_no_config():
    """Test AuthenticationProvider defaults when environment variables are unset."""
    with patch.dict(os.environ, {}, clear=True):
        provider = AuthenticationProvider()
        diag = provider.run_diagnostics()
        assert diag["configured"] == "no"
        assert diag["exists"] == "no"
        assert diag["size"] == 0
        assert diag["format_valid"] == "fail"

def test_authentication_provider_with_invalid_cookie_file(tmp_path):
    """Test AuthenticationProvider diagnostics on a malformed cookie file."""
    cookie_file = tmp_path / "invalid_cookies.txt"
    cookie_file.write_text("invalid content without header")
    
    with patch.dict(os.environ, {"CLIPFORGE_COOKIES_FILE": str(cookie_file)}):
        provider = AuthenticationProvider()
        diag = provider.run_diagnostics()
        assert diag["configured"] == "yes"
        assert diag["exists"] == "yes"
        assert diag["size"] > 0
        assert diag["format_valid"] == "fail"
        assert diag["has_auth_cookies"] == "no"

def test_authentication_provider_with_valid_cookie_file(tmp_path):
    """Test AuthenticationProvider diagnostics on a valid cookie file with some expected keys."""
    cookie_file = tmp_path / "valid_cookies.txt"
    cookie_file.write_text(
        "# Netscape HTTP Cookie File\n"
        ".youtube.com\tTRUE\t/\tTRUE\t1821767606\tSID\tsecret_sid_value\n"
        ".youtube.com\tTRUE\t/\tTRUE\t1821767606\tLOGIN_INFO\tsecret_login_info\n"
    )
    
    with patch.dict(os.environ, {"CLIPFORGE_COOKIES_FILE": str(cookie_file)}):
        provider = AuthenticationProvider()
        diag = provider.run_diagnostics()
        assert diag["configured"] == "yes"
        assert diag["exists"] == "yes"
        assert diag["format_valid"] == "pass"
        assert "SID" in diag["has_auth_cookies"]
        assert "LOGIN_INFO" in diag["has_auth_cookies"]
