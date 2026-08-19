import os
import time
import logging
import threading
from datetime import datetime, timedelta
from pathlib import Path
from .db import (
    init_db,
    get_setting,
    set_setting,
    get_videos_by_status,
    get_next_pending_job,
    update_job_status,
    add_job,
    get_job,
    add_generated_short,
    update_source_video_status
)
from .source_manager import sync_channels, download_video_source
from . import pretranscribe, clipper, captions
from .edit_plan import EditPlan

logger = logging.getLogger("ai_video_clipper.scheduler")

class SchedulerWorker:
    def __init__(self):
        self.running = False
        self.thread = None
        self._lock = threading.Lock()

    def start(self):
        with self._lock:
            if self.running:
                return
            self.running = True
            init_db()
            self.thread = threading.Thread(target=self._loop, daemon=True, name="ShortsFactoryScheduler")
            self.thread.start()
            logger.info("Background scheduler worker started.")

    def stop(self):
        with self._lock:
            self.running = False
            logger.info("Background scheduler worker stopping...")

    def trigger_once(self):
        try:
            logger.info("Manually triggering scheduler iteration...")
            sync_channels()
            run_mode = get_setting('run_mode', 'local')
            if run_mode == 'local':
                job = get_next_pending_job()
                if job:
                    set_setting('automation_status', 'PROCESSING')
                    self._process_job(job)
                    return
            set_setting('automation_status', 'WAITING')
            self._check_and_enqueue_next()
        except Exception as e:
            logger.exception("Error in manually triggered scheduler: %s", e)

    def _loop(self):
        while self.running:
            try:
                status = get_setting('automation_status', 'IDLE')
                if status in ('RUNNING', 'PROCESSING', 'RENDERING', 'PUBLISHING', 'WAITING'):
                    sync_channels()
                    run_mode = get_setting('run_mode', 'local')
                    if run_mode == 'local':
                        job = get_next_pending_job()
                        if job:
                            set_setting('automation_status', 'PROCESSING')
                            self._process_job(job)
                            continue
                    set_setting('automation_status', 'WAITING')
                    self._check_and_enqueue_next()
            except Exception as e:
                logger.exception("Error in scheduler loop: %s", e)
                set_setting('automation_status', 'FAILED')
            time.sleep(60)

    def _check_and_enqueue_next(self):

        try:
            shorts_per_day = int(get_setting('shorts_per_day', 3))
            if shorts_per_day <= 0:
                shorts_per_day = 3
        except (TypeError, ValueError):
            shorts_per_day = 3

        try:
            buffer_days = int(get_setting('buffer_days', 2))
            if buffer_days < 0:
                buffer_days = 2
        except (TypeError, ValueError):
            buffer_days = 2

        target_count = shorts_per_day * buffer_days
        
        # Count future scheduled shorts
        conn = clipper.Path(clipper.CLIPS_DIR).parent / 'shorts_factory.db'
        if not conn.exists():
            return
            
        import sqlite3
        db_conn = sqlite3.connect(str(conn))
        db_conn.row_factory = sqlite3.Row
        cursor = db_conn.cursor()
        now_str = datetime.utcnow().isoformat()
        
        cursor.execute(
            """SELECT COUNT(*) as cnt FROM generated_shorts 
               WHERE status IN ('scheduled', 'ready', 'uploading') 
               AND (scheduled_publish_time IS NULL OR scheduled_publish_time > ?)""",
            (now_str,)
        )
        row = cursor.fetchone()
        current_scheduled = row['cnt'] if row else 0
        db_conn.close()
        
        if current_scheduled >= target_count:
            logger.debug("Schedule is full (%d/%d scheduled). No new jobs needed.", current_scheduled, target_count)
            return
            
        logger.info("Schedule has gaps (%d/%d scheduled). Looking for new videos to process...", current_scheduled, target_count)
        
        # Get next new video
        new_videos = get_videos_by_status('new')
        if not new_videos:
            logger.info("No new videos found in database. Waiting for next channel sync.")
            return
            
        next_video = new_videos[0]
        video_id = next_video['id']
        
        # Check run mode
        run_mode = get_setting('run_mode', 'local')
        if run_mode == 'remote':
            import secrets
            import uuid
            import json
            from . import github_dispatch, jobs
            from .models import GenerateRequest, AspectRatio, FitMode
            
            # Generate callback token and job ID
            callback_token = secrets.token_hex(16)
            job_id = uuid.uuid4().hex
            
            # Construct default GenerateRequest
            req = GenerateRequest(
                video_url=next_video['url'],
                aspect_ratio=AspectRatio.NINE_16,
                fit_mode=FitMode.CROP,
                num_clips=3,
                clip_length=30,
                caption_style="bold_white"
            )
            req_json = req.model_dump_json()
            
            # Add to jobs table
            add_job(
                job_id=job_id,
                source_video_id=video_id,
                status='PENDING',
                request_json=req_json,
                callback_token=callback_token
            )
            
            # Get server_url
            server_url = get_setting("public_server_url")
            if not server_url:
                logger.error("Remote scheduler dispatch failed: 'public_server_url' setting is not configured.")
                update_job_status(job_id, 'FAILED', error="public_server_url not configured.")
                return
                
            success = github_dispatch.dispatch_workflow(job_id, callback_token, server_url)
            if success:
                logger.info("Automatically enqueued and dispatched remote job %s for video ID %s", job_id, video_id)
                # Register in-memory job placeholder for SSE /progress and /result
                job = jobs.Job(req)
                job.id = job_id
                job.status = "queued"
                job.stage = "queued"
                job.message = "Remote scheduler job dispatched to GitHub Actions."
                with jobs._JOBS_LOCK:
                    jobs._JOBS[job_id] = job
            else:
                logger.error("Failed to automatically dispatch remote job %s", job_id)
                update_job_status(job_id, 'FAILED', error="GitHub Actions dispatch failed.")
            return

        # Local mode: Download video
        download_path = download_video_source(video_id)
        if download_path:
            # Enqueue job
            add_job(job_id=video_id, source_video_id=video_id, status='PENDING')
            logger.info("Enqueued job %s for video ID %s", video_id, video_id)

    def _process_job(self, job: dict):
        job_id = job['id']
        video_id = job['source_video_id']
        logger.info("Processing scheduler job %s...", job_id)
        
        from .db import get_source_video
        video = get_source_video(video_id)
        if not video or not video['download_path']:
            update_job_status(job_id, 'FAILED', error="Source video not downloaded or missing from DB.")
            return
            
        source_path = Path(video['download_path'])
        if not source_path.exists():
            update_job_status(job_id, 'FAILED', error=f"Downloaded source video file not found at {source_path}")
            update_source_video_status(video_id, 'new')  # Reset status so we try downloading again
            return
            
        try:
            # Stage 1: Transcription
            update_job_status(job_id, 'TRANSCRIBING')
            logger.info("[%s] Transcribing source video...", job_id)
            
            source_id = source_path.stem
            # Get default device or CUDA
            device = get_setting('transcribe_device', 'cpu')
            
            # Get or run transcription
            transcript = pretranscribe.get_or_transcribe(
                source_mp4=source_path,
                source_id=source_id,
                device=device
            )
            words = transcript.get("words") or []
            
            # Stage 2: AI Editing Director planning
            update_job_status(job_id, 'EDITING')
            logger.info("[%s] Calling AI Director for editing instructions...", job_id)
            
            # Import director dynamically to avoid circular issues
            from .director import generate_edit_plan
            edit_plan = generate_edit_plan(transcript, video['title'])
            
            # Save the plan JSON to jobs
            edit_plan_json = edit_plan.model_dump_json()
            update_job_status(job_id, 'EDITING', edit_plan_json=edit_plan_json)
            
            # Stage 3: Rendering
            update_job_status(job_id, 'RENDERING')
            logger.info("[%s] Rendering composition from edit plan...", job_id)
            
            clip_id = f"shorts_{job_id}_{int(time.time())}"
            clip_dir = Path(clipper.CLIPS_DIR) / clip_id
            clip_dir.mkdir(parents=True, exist_ok=True)
            
            # Map transcripts and generate subtitles
            from .plan_executor import map_transcript_to_composed, get_composed_duration
            from .transcriber import _get_duration
            
            source_duration = _get_duration(source_path)
            composed_dur = get_composed_duration(edit_plan, source_duration)
            
            mapped_words = map_transcript_to_composed(words, edit_plan, source_duration)
            
            ass_path = clip_dir / "0.ass"
            width, height = 1080, 1920  # standard portrait
            
            captions.build_ass(
                words=mapped_words,
                style_preset="bold_white",
                video_w=width,
                video_h=height,
                out_path=ass_path,
                clip_start=0.0,
                fit_mode="crop"
            )
            
            opts = clipper.ClipOptions(
                aspect_ratio=clipper.AspectRatio.NINE_16,
                fit_mode=clipper.FitMode.CROP,
                ass_path=ass_path,
                clip_id=clip_id,
                index=0,
                edit_plan=edit_plan
            )
            
            output_file = clipper.generate_clip(source_path, 0.0, composed_dur, opts)
            logger.info("[%s] Render completed: %s", job_id, output_file)
            
            # Stage 4: Quality Control Pipeline
            update_job_status(job_id, 'QUALITY_CHECK')
            from .qc import run_quality_check
            qc_passed, qc_error = run_quality_check(output_file, width, height, composed_dur)
            if not qc_passed:
                raise ValueError(f"Quality Control failed: {qc_error}")
                
            # Stage 5: SEO Optimization
            update_job_status(job_id, 'SEO')
            from .seo import generate_metadata
            seo_meta = generate_metadata(transcript, video['title'])
            
            # Stage 6: Determine next publish slot and schedule
            publish_time = self._calculate_next_slot()
            
            # Save generated short details
            add_generated_short(
                short_id=clip_id,
                source_video_id=video_id,
                clip_index=0,
                edit_plan_json=edit_plan_json,
                file_path=str(output_file.resolve()),
                status='ready',  # ready for upload
                title=seo_meta.get('title', video['title']),
                description=seo_meta.get('description', ''),
                tags=','.join(seo_meta.get('tags', [])),
                scheduled_publish_time=publish_time.isoformat()
            )
            
            # Update source video status to processed
            update_source_video_status(video_id, 'processed')
            update_job_status(job_id, 'READY')
            
            # Stage 7: Clean up downloaded source video (dlt the videos!)
            try:
                if source_path.exists():
                    os.remove(source_path)
                    logger.info("[%s] Deleted source video file %s to save space.", job_id, source_path)
            except Exception as e:
                logger.warning("[%s] Failed to delete source video file: %s", job_id, e)
                
            # Stage 8: Trigger YouTube Upload (asynchronous OAuth upload)
            from .youtube import upload_scheduled_short
            upload_scheduled_short(clip_id)
            
        except Exception as e:
            logger.exception("Error processing scheduler job %s: %s", job_id, e)
            update_job_status(job_id, 'FAILED', error=str(e))

    def _calculate_next_slot(self) -> datetime:
        """
        Calculates the next available publication slot timestamp based on shorts_per_day setting.
        """
        shorts_per_day = get_setting('shorts_per_day', 3)
        interval_hours = 24.0 / shorts_per_day
        
        # Check database for latest scheduled publish time
        conn = clipper.Path(clipper.CLIPS_DIR).parent / 'shorts_factory.db'
        import sqlite3
        db_conn = sqlite3.connect(str(conn))
        db_conn.row_factory = sqlite3.Row
        cursor = db_conn.cursor()
        
        cursor.execute(
            """SELECT MAX(scheduled_publish_time) as max_time FROM generated_shorts 
               WHERE status IN ('scheduled', 'ready', 'uploading')"""
        )
        row = cursor.fetchone()
        db_conn.close()
        
        now = datetime.utcnow()
        if row and row['max_time']:
            try:
                latest = datetime.fromisoformat(row['max_time'])
                if latest > now:
                    return latest + timedelta(hours=interval_hours)
            except Exception:
                pass
                
        # Fallback if no future scheduled shorts: schedule for now + 1 hour
        return now + timedelta(hours=1.0)
