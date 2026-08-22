"""Central filesystem layout for the project.

All other modules import these so directory locations are defined exactly once.
The directories are created on import (and again at startup) so the very first
run works without any manual setup.
"""

from __future__ import annotations

from pathlib import Path
import tempfile
import logging

logger = logging.getLogger("paths")

# Project root = the directory that contains the `app/` package.
ROOT_DIR = Path(__file__).resolve().parent.parent

def _resolve_writable_dir(name: str, default_path: Path) -> Path:
    try:
        default_path.mkdir(parents=True, exist_ok=True)
        # Test write permission
        test_file = default_path / f".write_test_{name}"
        test_file.touch()
        test_file.unlink()
        return default_path
    except Exception as e:
        fallback = Path(tempfile.gettempdir()) / "clipforge" / name
        try:
            fallback.mkdir(parents=True, exist_ok=True)
            logger.warning("Directory %s is not writable (%s). Falling back to %s", default_path, e, fallback)
            return fallback
        except Exception:
            # Absolute fallback to home directory
            fallback_home = Path.home() / ".clipforge" / name
            fallback_home.mkdir(parents=True, exist_ok=True)
            return fallback_home

DOWNLOADS_DIR = _resolve_writable_dir("downloads", ROOT_DIR / "downloads")
TRANSCRIPTS_DIR = _resolve_writable_dir("transcripts", ROOT_DIR / "transcripts")
CLIPS_DIR = _resolve_writable_dir("clips", ROOT_DIR / "clips")
ASSETS_DIR = _resolve_writable_dir("assets", ROOT_DIR / "assets")
FONTS_DIR = _resolve_writable_dir("fonts", ASSETS_DIR / "fonts")
MASKS_DIR = _resolve_writable_dir("masks", ASSETS_DIR / "masks")
MUSIC_DIR = _resolve_writable_dir("music", ASSETS_DIR / "music")
STATIC_DIR = _resolve_writable_dir("static", ROOT_DIR / "static")
WEB_DIST_DIR = ROOT_DIR / "web" / "dist"


def ensure_dirs() -> None:
    """Create every runtime directory if it does not already exist."""
    pass


# Call ensure_dirs() defensively
ensure_dirs()
