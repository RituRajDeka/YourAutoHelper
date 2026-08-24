import sys
import os
import json
import logging
from pathlib import Path
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock

# Set mock environment variables before imports
os.environ["GROQ_API_KEY"] = "mock_groq_api_key_value"

# Ensure parent directory is in sys.path
project_root = Path(__file__).resolve().parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

# Set up logging to stdout
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("e2e_test")

# Redirect DB_PATH to test_factory.db
import app.db
app.db.DB_PATH = app.db.ROOT_DIR / 'test_factory.db'
if app.db.DB_PATH.exists():
    app.db.DB_PATH.unlink()
app.db.init_db()
logger.info(f"Initialized test SQLite database at {app.db.DB_PATH}")

import app.transcriber
from app import director
from app import omniroute
from app import clipper
from app.clipper import ClipOptions
from app.models import AspectRatio, FitMode
from app import qc
from app import seo
from app import youtube

def run_e2e_test():
    logger.info("--- Starting E2E YouTube Shorts Factory Pipeline Test ---")

    # 1. Check for the local test video
    video_path = Path("/home/laophan/clipforge-work/Testing If You Can Blow Your Own Sail - Mark Rober (1080p, h264).mp4")
    if not video_path.exists():
        logger.error(f"Test video not found at {video_path}")
        sys.exit(1)
    logger.info(f"Found test video at: {video_path}")

    # Define a mock transcript to make the test run quickly
    mock_transcript = {
        "text": "This is a test of the video clipper composition pipeline.",
        "segments": [
            {"start": 10.0, "end": 20.0, "text": "This is a test of the video clipper composition pipeline."}
        ],
        "words": [
            {"word": "This", "start": 10.0, "end": 10.5, "probability": 0.99},
            {"word": "is", "start": 10.5, "end": 11.0, "probability": 0.99},
            {"word": "a", "start": 11.0, "end": 11.2, "probability": 0.99},
            {"word": "test", "start": 11.2, "end": 12.0, "probability": 0.99},
            {"word": "of", "start": 12.0, "end": 12.2, "probability": 0.99},
            {"word": "the", "start": 12.2, "end": 12.5, "probability": 0.99},
            {"word": "video", "start": 12.5, "end": 13.0, "probability": 0.99},
            {"word": "clipper", "start": 13.0, "end": 13.5, "probability": 0.99},
            {"word": "composition", "start": 13.5, "end": 14.5, "probability": 0.99},
            {"word": "pipeline.", "start": 14.5, "end": 15.5, "probability": 0.99},
            {"word": "Let's", "start": 15.5, "end": 16.0, "probability": 0.99},
            {"word": "make", "start": 16.0, "end": 16.5, "probability": 0.99},
            {"word": "sure", "start": 16.5, "end": 17.0, "probability": 0.99},
            {"word": "this", "start": 17.0, "end": 17.5, "probability": 0.99},
            {"word": "works", "start": 17.5, "end": 18.0, "probability": 0.99},
            {"word": "correctly", "start": 18.0, "end": 19.0, "probability": 0.99},
            {"word": "now.", "start": 19.0, "end": 20.0, "probability": 0.99}
        ]
    }

    # Mock the transcriber function to return our mock transcript
    with patch('app.transcriber.transcribe_video', return_value=mock_transcript) as mock_transcribe:
        logger.info("Step 1: Transcribing test video (mocked)...")
        transcript = app.transcriber.transcribe_video(video_path, "e2e_job")
        logger.info("Transcribed text: %s", transcript["text"])

    # 3. Passes the transcription results to app.director.generate_edit_plan
    from app.edit_plan import EditPlan, Cut, Zoom, SpeedChange
    mock_edit_plan = EditPlan(
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

    with patch('app.omniroute.completion', return_value=mock_edit_plan) as mock_completion:
        logger.info("Step 2: Generating EditPlan...")
        edit_plan = director.generate_edit_plan(transcript, "Mark Rober Sail Test")
        logger.info("Generated EditPlan cuts: %s", edit_plan.cuts)

    # 4. Passes the EditPlan to app.clipper.generate_clip to render the output short video (using FitMode.CROP)
    logger.info("Step 3: Rendering the output short video via clipper...")
    clip_id = "e2e_short"
    out_dir = (app.paths.CLIPS_DIR / clip_id).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    ass_path = out_dir / "e2e.ass"

    # Build caption file
    from app.captions import build_ass
    build_ass(
        words=[],
        style_preset="bold_white",
        video_w=1080,
        video_h=1920,
        out_path=ass_path,
        fit_mode="crop"
    )

    opts = ClipOptions(
        aspect_ratio=AspectRatio.NINE_16,
        fit_mode=FitMode.CROP,
        ass_path=ass_path,
        clip_id=clip_id,
        index=0,
        edit_plan=edit_plan
    )

    clip_output_path = clipper.generate_clip(
        source_path=video_path,
        start=10.0,
        end=20.0,
        opts=opts
    )
    logger.info(f"Rendered video successfully at: {clip_output_path}")

    # 5. Runs the rendered video file through the Quality Control pipeline
    logger.info("Step 4: Running Quality Control checks...")
    qc_fn = getattr(qc, "validate_video_quality", None) or getattr(qc, "run_quality_check", None)
    if not qc_fn:
        raise AttributeError("No QC validation function found in app.qc module.")
    
    # Expected dimensions are 1080x1920, expected duration is 8.5s
    qc_passed, qc_error = qc_fn(clip_output_path, 1080, 1920, 8.5)
    logger.info(f"QC Passed: {qc_passed}, Error (if any): {qc_error}")
    assert qc_passed, f"QC check failed: {qc_error}"

    # 6. Runs app.seo.generate_seo_metadata to generate optimized title, description, and tags
    logger.info("Step 5: Generating SEO metadata...")
    seo_fn = getattr(seo, "generate_seo_metadata", None) or getattr(seo, "generate_metadata", None)
    if not seo_fn:
        raise AttributeError("No SEO metadata generation function found in app.seo module.")
    
    # We mock omniroute.completion inside to return static SEO metadata
    mock_seo_result = seo.SEOMetadata(
        title="Can You Blow Your Own Sail? ⛵ #shorts",
        description="Testing if you can blow your own sail with Mark Rober! #shorts #viral #science",
        tags=["shorts", "viral", "science", "mark rober"]
    )
    with patch('app.omniroute.completion', return_value=mock_seo_result):
        seo_meta = seo_fn(transcript, "Mark Rober Sail Test")
    logger.info(f"Generated SEO Metadata: {seo_meta}")

    # 7. Saves the short in the database generated_shorts table
    logger.info("Step 6: Saving short to SQLite database...")
    short_id = "short_e2e_1"
    scheduled_time = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    app.db.add_generated_short(
        short_id=short_id,
        source_video_id="mark_rober_sail",
        clip_index=0,
        edit_plan_json=edit_plan.model_dump_json(),
        file_path=str(clip_output_path),
        status="ready",
        title=seo_meta["title"],
        description=seo_meta["description"],
        tags=",".join(seo_meta["tags"]),
        scheduled_publish_time=scheduled_time
    )
    # Also save YouTube OAuth mock token
    app.db.set_setting("youtube_oauth_token", json.dumps({
        "token": "mock_token",
        "refresh_token": "mock_refresh",
        "expiry": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    }))

    # Verify database insertion
    saved_short = app.db.get_generated_short(short_id)
    assert saved_short is not None, "Failed to retrieve saved short from database."
    logger.info(f"Database record verification - Status: {saved_short['status']}, Title: {saved_short['title']}")

    # 8. Mocks the actual Google YouTube client API, but runs app.youtube.upload_scheduled_short
    logger.info("Step 7: Testing YouTube scheduled upload...")
    
    mock_youtube_client = MagicMock()
    mock_videos = MagicMock()
    mock_insert = MagicMock()
    
    mock_youtube_client.videos.return_value = mock_videos
    mock_videos.insert.return_value = mock_insert
    mock_insert.next_chunk.return_value = (None, {"id": "mock_youtube_video_id"})

    mock_creds_inst = MagicMock()
    mock_creds_inst.expired = False
    mock_creds_inst.refresh_token = "mock_refresh"

    with patch('app.youtube.build', return_value=mock_youtube_client), \
         patch('app.youtube.MediaFileUpload', MagicMock()), \
         patch('app.youtube.OAuth2Credentials') as mock_oauth_creds:
         
         mock_oauth_creds.from_authorized_user_info.return_value = mock_creds_inst
         youtube.upload_scheduled_short(short_id)

    # Verify that the short's status was updated to 'scheduled'
    updated_short = app.db.get_generated_short(short_id)
    logger.info(f"Post-Upload Database status: {updated_short['status']}, YouTube Video ID: {updated_short['youtube_id']}")
    assert updated_short['status'] == 'scheduled', f"Expected status 'scheduled', got '{updated_short['status']}'"
    assert updated_short['youtube_id'] == 'mock_youtube_video_id', f"Expected YouTube ID 'mock_youtube_video_id', got '{updated_short['youtube_id']}'"

    logger.info("--- E2E PIPELINE RUN COMPLETED SUCCESSFULLY! ---")

if __name__ == "__main__":
    try:
        run_e2e_test()
    except Exception as e:
        logger.exception("E2E Test Failed!")
        sys.exit(1)
