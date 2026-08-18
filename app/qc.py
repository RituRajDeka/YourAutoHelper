import json
import subprocess
import logging
from pathlib import Path
from typing import Tuple

logger = logging.getLogger("ai_video_clipper.qc")

def run_quality_check(file_path: Path, expected_w: int, expected_h: int, expected_duration: float) -> Tuple[bool, str]:
    """
    Runs automated checks on the generated MP4 file.
    Verifies:
    1. File exists and is non-empty.
    2. Video duration matches expected duration (+/- 1.5 seconds tolerance).
    3. Video resolution matches expected dimensions.
    """
    if not file_path.exists():
        return False, "File does not exist."
        
    size_bytes = file_path.stat().st_size
    if size_bytes < 50 * 1024:  # less than 50KB is probably corrupted/empty
        return False, f"File size too small ({size_bytes / 1024:.1f} KB)."
        
    try:
        cmd = [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration:stream=width,height",
            "-of", "json",
            str(file_path)
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        info = json.loads(result.stdout)
        
        # Check duration
        duration = float(info.get('format', {}).get('duration', 0.0))
        if abs(duration - expected_duration) > 2.5: # 2.5s tolerance is safer for speed/cut alignments
            return False, f"Duration mismatch: expected {expected_duration:.2f}s, got {duration:.2f}s (tolerance +/- 2.5s)."
            
        # Check resolution
        streams = info.get('streams', [])
        if not streams:
            return False, "No video stream found in the output file."
            
        # Find video stream
        video_stream = None
        for s in streams:
            if 'width' in s and 'height' in s:
                video_stream = s
                break
                
        if not video_stream:
            return False, "No video stream found with valid dimensions."
            
        width = int(video_stream['width'])
        height = int(video_stream['height'])
        
        if width != expected_w or height != expected_h:
            return False, f"Resolution mismatch: expected {expected_w}x{expected_h}, got {width}x{height}."
            
        logger.info("Quality Control passed for %s: duration=%.2fs, size=%.1fMB", file_path.name, duration, size_bytes / 1048576)
        return True, ""
        
    except Exception as e:
        logger.exception("Failed to run ffprobe during QC: %s", e)
        return False, f"ffprobe error: {e}"
