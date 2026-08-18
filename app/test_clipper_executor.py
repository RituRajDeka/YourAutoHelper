import sys
import os
import subprocess
from pathlib import Path

# Add project root to sys.path to allow running as a script directly
project_root = Path(__file__).resolve().parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from app.clipper import generate_clip, ClipOptions
from app.models import AspectRatio, FitMode
from app.edit_plan import EditPlan, Cut, Zoom, SpeedChange
from app.captions import build_ass
from app.paths import CLIPS_DIR

def run_test():
    print("Starting integration test for clipper and plan_executor...")
    
    # 3. Sets up a test run using the video file
    video_path = Path("/home/laophan/clipforge-work/Testing If You Can Blow Your Own Sail - Mark Rober (1080p, h264).mp4")
    if not video_path.exists():
        print(f"Error: Video file not found at {video_path}")
        sys.exit(1)
        
    print(f"Found test video at: {video_path}")
    
    # 4. Defines an EditPlan
    edit_plan = EditPlan(
        cuts=[
            Cut(start_time=10.0, end_time=20.0)
        ],
        zooms=[
            Zoom(time=12.0, duration=3.0, scale=1.5, x=0.5, y=0.5)
        ],
        speed_changes=[
            SpeedChange(start_time=15.0, end_time=18.0, speed=2.0)
        ]
    )
    
    # Let's verify the calculated composed duration
    from app.plan_executor import get_composed_duration
    from app.transcriber import _get_duration
    source_duration = _get_duration(video_path)
    expected_duration = 8.5
    calc_duration = get_composed_duration(edit_plan, source_duration)
    print(f"Calculated composed duration: {calc_duration} seconds (expected: {expected_duration})")
    
    # Create output dirs and build ASS subtitle files
    # FitMode.CROP
    crop_clip_id = "test_clipper_executor_crop"
    crop_out_dir = (CLIPS_DIR / crop_clip_id).resolve()
    crop_out_dir.mkdir(parents=True, exist_ok=True)
    crop_ass_path = crop_out_dir / "crop.ass"
    build_ass(
        words=[],
        style_preset="bold_white",
        video_w=1080,
        video_h=1920,
        out_path=crop_ass_path,
        fit_mode="crop"
    )
    
    opts_crop = ClipOptions(
        aspect_ratio=AspectRatio.NINE_16,
        fit_mode=FitMode.CROP,
        ass_path=crop_ass_path,
        clip_id=crop_clip_id,
        index=0,
        edit_plan=edit_plan
    )
    
    # FitMode.SQUARE
    square_clip_id = "test_clipper_executor_square"
    square_out_dir = (CLIPS_DIR / square_clip_id).resolve()
    square_out_dir.mkdir(parents=True, exist_ok=True)
    square_ass_path = square_out_dir / "square.ass"
    build_ass(
        words=[],
        style_preset="bold_white",
        video_w=1080,
        video_h=1920,
        out_path=square_ass_path,
        fit_mode="square"
    )
    
    opts_square = ClipOptions(
        aspect_ratio=AspectRatio.NINE_16,
        fit_mode=FitMode.SQUARE,
        ass_path=square_ass_path,
        clip_id=square_clip_id,
        index=0,
        edit_plan=edit_plan
    )
    
    # 5. Run generate_clip
    print("Running generate_clip with FitMode.CROP...")
    crop_output_path = generate_clip(
        source_mp4=video_path,
        start=10.0,
        end=20.0,
        opts=opts_crop
    )
    print(f"Generated crop output at: {crop_output_path}")
    
    print("Running generate_clip with FitMode.SQUARE...")
    square_output_path = generate_clip(
        source_mp4=video_path,
        start=10.0,
        end=20.0,
        opts=opts_square
    )
    print(f"Generated square output at: {square_output_path}")
    
    # 6. Verify outputs and probe using ffprobe
    def probe_duration(file_path: Path) -> float:
        cmd = [
            "ffprobe",
            "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(file_path)
        ]
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True)
        return float(res.stdout.strip())
        
    print("Verifying Crop output duration...")
    if not crop_output_path.exists():
        raise AssertionError("Crop output file does not exist")
    crop_duration = probe_duration(crop_output_path)
    print(f"Probed Crop duration: {crop_duration:.3f}s")
    assert abs(crop_duration - expected_duration) < 0.1, f"Crop duration {crop_duration} not within 0.1s of {expected_duration}"
    
    print("Verifying Square output duration...")
    if not square_output_path.exists():
        raise AssertionError("Square output file does not exist")
    square_duration = probe_duration(square_output_path)
    print(f"Probed Square duration: {square_duration:.3f}s")
    assert abs(square_duration - expected_duration) < 0.1, f"Square duration {square_duration} not within 0.1s of {expected_duration}"
    
    print("\nALL INTEGRATION TESTS PASSED SUCCESSFULLY!")

if __name__ == "__main__":
    try:
        run_test()
    except Exception as e:
        print(f"\nTEST FAILED: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
