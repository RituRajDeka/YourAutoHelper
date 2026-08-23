import os
import sys
import argparse
import logging
import requests
from pathlib import Path
from typing import Optional, Dict, Any, List

# Ensure parent directory is in sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import downloader, pretranscribe, selector, captions, clipper, qc, seo, storage
from app.models import GenerateRequest, AspectRatio, FitMode, Device, ClipGenerationError
from app.paths import DOWNLOADS_DIR, CLIPS_DIR
from app.clipper import target_size, ClipOptions

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("gha_worker")

def main():
    parser = argparse.ArgumentParser(description="GHA Runner Script for ClipForge")
    parser.add_argument("--job-id", required=True, help="Job ID")
    parser.add_argument("--callback-token", required=True, help="Callback authentication token")
    parser.add_argument("--server-url", required=True, help="Server URL")
    args = parser.parse_args()

    job_id = args.job_id
    callback_token = args.callback_token
    server_url = args.server_url.rstrip("/")

    # Progress callback helper
    def report_progress(
        status: str,
        progress: float,
        message: str,
        error: Optional[str] = None,
        s3_url: Optional[str] = None,
        clips: Optional[List[Dict[str, Any]]] = None,
        seo_metadata: Optional[Dict[str, Any]] = None
    ):
        url = f"{server_url}/api/jobs/{job_id}/callback?token={callback_token}"
        payload = {
            "status": status,
            "progress": round(progress, 2),
            "message": message,
            "error": error,
            "s3_url": s3_url,
            "clips": clips,
            "seo_metadata": seo_metadata,
            "gha_run_id": os.environ.get("GITHUB_RUN_ID")
        }
        headers = {
            "Authorization": f"Bearer {callback_token}",
            "Content-Type": "application/json"
        }
        logger.info("Sending callback: status=%s, progress=%.2f, message=%s", status, progress, message)
        # Attempt POST first, fall back to PATCH
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=15)
            if resp.status_code >= 400:
                logger.error(f"POST failed with status {resp.status_code}: {resp.text}")
            resp.raise_for_status()
        except Exception as e:
            logger.warning("POST callback failed, trying PATCH: %s", e)
            resp = requests.patch(url, headers=headers, json=payload, timeout=15)
            if resp.status_code >= 400:
                logger.error(f"PATCH failed with status {resp.status_code}: {resp.text}")
            resp.raise_for_status()

    try:
        # Step 1: Fetch configuration from server
        logger.info("Fetching configuration for job %s...", job_id)
        config_url = f"{server_url}/api/jobs/{job_id}/config"
        headers = {"Authorization": f"Bearer {callback_token}"}
        resp = requests.get(config_url, headers=headers, timeout=15)
        resp.raise_for_status()
        config = resp.json()

        req_data = config.get("req")
        if not req_data:
            raise ValueError("No 'req' field found in configuration response.")

        req = GenerateRequest.model_validate(req_data)
        groq_api_key = config.get("groq_api_key")
        edit_plan_data = config.get("edit_plan") or req.edit_plan

        # Set environment variables
        if groq_api_key:
            os.environ["GROQ_API_KEY"] = groq_api_key.strip()
            logger.info("GROQ_API_KEY set from configuration.")

        youtube_cookies = config.get("youtube_cookies")
        if youtube_cookies:
            cookies_str = youtube_cookies.strip()
            # Ensure the Netscape header line is present so yt-dlp doesn't throw a parsing error
            if not cookies_str.startswith("# Netscape HTTP Cookie File") and not cookies_str.startswith("# HTTP Cookie File"):
                cookies_str = "# Netscape HTTP Cookie File\n" + cookies_str
                
            cookies_path = DOWNLOADS_DIR / "cookies.txt"
            DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)
            with open(cookies_path, "w", encoding="utf-8") as f:
                f.write(cookies_str)
            os.environ["CLIPFORGE_COOKIES_FILE"] = str(cookies_path)
            logger.info("YouTube cookies saved and CLIPFORGE_COOKIES_FILE set.")



        # Sync S3 environment variables to database settings
        from app import db
        db.init_db()
        env_to_settings = {
            "STORAGE_PROVIDER": "storage_provider",
            "S3_BUCKET_NAME": "s3_bucket_name",
            "S3_ACCESS_KEY": "s3_access_key",
            "AWS_ACCESS_KEY_ID": "s3_access_key",
            "S3_SECRET_KEY": "s3_secret_key",
            "AWS_SECRET_ACCESS_KEY": "s3_secret_key",
            "S3_ENDPOINT_URL": "s3_endpoint_url",
            "S3_REGION": "s3_region",
            "AWS_DEFAULT_REGION": "s3_region",
            "S3_PUBLIC_URL_PREFIX": "s3_public_url_prefix",
        }
        for env_var, setting_key in env_to_settings.items():
            val = os.environ.get(env_var)
            if val:
                db.set_setting(setting_key, val)

        # Step 2: Download Step
        report_progress("downloading", 10.0, "Starting download...")
        source_id = req.upload_id or req.download_id
        s3_url = config.get("s3_url")
        source_video_id = config.get("source_video", {}).get("source_video_id")
        
        s3_settings = config.get("s3_settings", {})
        use_s3_source = False
        s3_bucket = s3_settings.get("s3_bucket_name")
        s3_key = None
        
        if s3_settings.get("storage_provider") == "s3" and s3_bucket:
            # Check if this video (by ID or URL) already exists in S3
            s3_client_kwargs = {}
            if s3_settings.get("s3_access_key"):
                s3_client_kwargs['aws_access_key_id'] = s3_settings["s3_access_key"]
            if s3_settings.get("s3_secret_key"):
                s3_client_kwargs['aws_secret_access_key'] = s3_settings["s3_secret_key"]
            if s3_settings.get("s3_endpoint_url"):
                s3_client_kwargs['endpoint_url'] = s3_settings["s3_endpoint_url"]
            if s3_settings.get("s3_region"):
                s3_client_kwargs['region_name'] = s3_settings["s3_region"]
            if s3_settings.get("s3_endpoint_url"):
                from botocore.client import Config as BotoConfig
                s3_client_kwargs['config'] = BotoConfig(
                    signature_version='s3v4',
                    request_checksum_calculation="when_required",
                    response_checksum_validation="when_required"
                )
                
            try:
                import boto3
                s3_check_client = boto3.client('s3', **s3_client_kwargs)
                possible_keys = []
                if s3_url:
                    if "sources/" in s3_url:
                        possible_keys.append("sources/" + s3_url.split("sources/")[1])
                    else:
                        possible_keys.append(s3_url.split(s3_bucket + "/")[-1])
                if source_video_id:
                    possible_keys.append(f"sources/{source_video_id}.mp4")
                    possible_keys.append(f"{source_video_id}.mp4")
                
                for key in possible_keys:
                    try:
                        s3_check_client.head_object(Bucket=s3_bucket, Key=key)
                        s3_key = key
                        use_s3_source = True
                        logger.info("Found existing source video in S3 bucket: %s/%s", s3_bucket, s3_key)
                        break
                    except Exception:
                        continue
            except Exception as check_err:
                logger.warning("Failed to check existing S3 source files: %s", check_err)

        if use_s3_source:
            logger.info("Downloading source file from S3: %s/%s", s3_bucket, s3_key)
            DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)
            video_id = source_video_id or "source_video"
            source_path = DOWNLOADS_DIR / f"{video_id}.mp4"
            
            import boto3
            from botocore.client import Config as BotoConfig
            s3_client_kwargs = {}
            if s3_settings.get("s3_access_key"):
                s3_client_kwargs['aws_access_key_id'] = s3_settings["s3_access_key"]
            if s3_settings.get("s3_secret_key"):
                s3_client_kwargs['aws_secret_access_key'] = s3_settings["s3_secret_key"]
            if s3_settings.get("s3_endpoint_url"):
                s3_client_kwargs['endpoint_url'] = s3_settings["s3_endpoint_url"]
            if s3_settings.get("s3_region"):
                s3_client_kwargs['region_name'] = s3_settings["s3_region"]
            if s3_settings.get("s3_endpoint_url"):
                s3_client_kwargs['config'] = BotoConfig(
                    signature_version='s3v4',
                    request_checksum_calculation="when_required",
                    response_checksum_validation="when_required"
                )
                
            s3_client = boto3.client('s3', **s3_client_kwargs)
            s3_client.download_file(s3_bucket, s3_key, str(source_path))
            logger.info("Source file downloaded successfully from S3: %s", source_path)
        elif source_id:
            logger.info("Downloading source file from server for id: %s", source_id)
            DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)
            source_path = DOWNLOADS_DIR / f"{source_id}.mp4"
            download_url = f"{server_url}/api/download/{source_id}/video"
            download_headers = {"Authorization": f"Bearer {callback_token}"}
            
            with requests.get(download_url, headers=download_headers, stream=True, timeout=30) as r:
                r.raise_for_status()
                with open(source_path, "wb") as f:
                    for chunk in r.iter_content(chunk_size=8192):
                        f.write(chunk)
            logger.info("Source file downloaded successfully: %s", source_path)
        else:
            logger.info("Downloading from URL: %s", req.video_url)
            downloader_mode = config.get("downloader_mode", "yt-dlp")
            source_path = downloader.download_video(req.video_url, downloader_mode=downloader_mode)

        report_progress("downloading", 25.0, "Download complete.")

        # Step 3: Transcribe Step
        report_progress("transcribing", 30.0, "Starting transcription...")
        
        def on_transcribe_progress(frac: float, msg: str):
            prog = 30.0 + (frac * 40.0)
            report_progress("transcribing", prog, f"Transcribing: {msg}")

        device_val = req.device.value if hasattr(req.device, "value") else str(req.device)
        transcript = pretranscribe.get_or_transcribe(
            source_path=source_path,
            source_id=source_path.stem,
            device=device_val,
            progress=on_transcribe_progress,
            language=req.language
        )
        report_progress("transcribing", 70.0, "Transcription complete.")

        # Step 4: Analyze / Select Step
        report_progress("selecting", 72.0, "Analyzing transcript...")
        if edit_plan_data:
            from app.edit_plan import EditPlan
            from app.plan_executor import get_composed_duration
            from app.transcriber import _get_duration

            edit_plan = EditPlan.model_validate(edit_plan_data)
            source_duration = _get_duration(source_path)
            composed_dur = get_composed_duration(edit_plan, source_duration)
            
            windows = [{
                "index": 0,
                "title": "Custom Edit Plan",
                "start": 0.0,
                "end": composed_dur
            }]
            logger.info("Using custom AI edit plan with duration: %.2f", composed_dur)
        else:
            windows = selector.select_clips(transcript, req.num_clips, req.clip_length)
            if not windows:
                raise ClipGenerationError(
                    "Could not find any suitable clip windows in this video. "
                    "Try a longer video or fewer clips."
                )
            logger.info("Found %d clip windows.", len(windows))

        report_progress("selecting", 75.0, f"Analysis complete. Found {len(windows)} clip(s).")

        # Step 5: Render Step
        report_progress("rendering", 80.0, "Rendering starting...")
        
        words = transcript.get("words") or []
        caption_overrides = req.caption_overrides.model_dump() if req.caption_overrides else None
        cinematic = req.cinematic.model_dump() if req.cinematic else None
        
        music_path = None
        if req.music_track:
            try:
                music_path = music.resolve_track(req.music_track)
            except Exception as exc:
                logger.warning("Music track %r not found; skipping: %s", req.music_track, exc)

        width, height = target_size(req.aspect_ratio, req.fit_mode)
        clip_dir = CLIPS_DIR / job_id
        clip_dir.mkdir(parents=True, exist_ok=True)

        results = []
        for index, win in enumerate(windows):
            start = float(win["start"])
            end = float(win["end"])
            
            prog = 80.0 + (index / len(windows)) * 10.0
            report_progress("rendering", prog, f"Rendering clip {index + 1} of {len(windows)}...")

            if edit_plan_data:
                from app.edit_plan import EditPlan
                from app.plan_executor import map_transcript_to_composed
                from app.transcriber import _get_duration

                edit_plan = EditPlan.model_validate(edit_plan_data)
                source_duration = _get_duration(source_path)
                clip_words = map_transcript_to_composed(words, edit_plan, source_duration)
                clip_start = 0.0
            else:
                edit_plan = None
                clip_words = [w for w in words if w["end"] > start and w["start"] < end]
                clip_start = start

            ass_path = clip_dir / f"{index}.ass"
            captions.build_ass(
                words=clip_words,
                style_preset=req.caption_style,
                video_w=width,
                video_h=height,
                out_path=ass_path,
                clip_start=clip_start,
                overrides=caption_overrides,
                fit_mode=req.fit_mode.value
            )

            opts = ClipOptions(
                aspect_ratio=req.aspect_ratio,
                fit_mode=req.fit_mode,
                ass_path=ass_path,
                clip_id=job_id,
                index=index,
                bar_text=req.bar_text,
                bar_text_color=req.bar_text_color or "#FFFFFF",
                bar_text_anim=req.bar_text_anim or "none",
                cinematic=cinematic,
                music_path=music_path,
                music_volume=req.music_volume if req.music_volume is not None else 35.0,
                music_duck=req.music_duck if req.music_duck is not None else 70.0,
                music_start=req.music_start if req.music_start is not None else 0.0,
                signature=req.signature.model_dump() if req.signature else None,
                edit_plan=edit_plan
            )
            clipper.generate_clip(source_path, start, end, opts)

            forced = (req.language or "").strip().lower()
            caption_language = forced if forced and forced != "auto" else transcript.get("language")
            from app.jobs import download_filename
            title = win.get("title") or f"Clip {index + 1}"
            clip = {
                "index": index,
                "title": title,
                "start": round(start, 2),
                "end": round(end, 2),
                "url": f"/clips/{job_id}/{index}.mp4",
                "language": caption_language,
                "filename": download_filename(title, caption_language, index),
            }
            results.append(clip)

        # Delete raw downloaded source video to save GHA disk space
        if source_path.exists():
            try:
                source_path.unlink()
                logger.info("Deleted raw source video %s to save space.", source_path)
            except Exception as e:
                logger.warning("Could not delete source video: %s", e)

        report_progress("rendering", 90.0, "Rendering complete.")

        # Step 6: QC Step
        report_progress("qc", 92.0, "Running Quality Control...")
        for index, win in enumerate(windows):
            clip_file_path = CLIPS_DIR / job_id / f"{index}.mp4"
            expected_duration = float(win["end"]) - float(win["start"])
            passed, reason = qc.run_quality_check(clip_file_path, width, height, expected_duration)
            if not passed:
                raise ClipGenerationError(f"Quality Check failed for clip {index}: {reason}")
        logger.info("All quality checks passed.")

        # Step 7: SEO Step
        report_progress("seo", 95.0, "Generating SEO metadata...")
        original_title = req.upload_name or (req.video_url if req.video_url else "Video")
        seo_metadata = seo.generate_metadata(transcript, original_title)
        logger.info("SEO metadata generated successfully.")

        # Step 8: Upload Step
        report_progress("uploading", 98.0, "Uploading rendered clips to S3...")
        storage_provider = storage.get_storage_provider()
        uploaded_urls = []
        for index, clip in enumerate(results):
            local_path = str(CLIPS_DIR / job_id / f"{index}.mp4")
            remote_name = f"clips/{job_id}/{index}.mp4"
            public_url = storage_provider.upload_file(local_path, remote_name)
            clip["url"] = public_url
            uploaded_urls.append(public_url)
            logger.info("Uploaded clip %d to %s", index, public_url)

        s3_url = uploaded_urls[0] if uploaded_urls else ""

        # Step 9: Callback Step
        logger.info("Job %s completed successfully.", job_id)
        report_progress(
            status="completed",
            progress=100.0,
            message="Job completed successfully.",
            s3_url=s3_url,
            clips=results,
            seo_metadata=seo_metadata
        )

    except Exception as e:
        logger.exception("GHA Worker execution failed: %s", e)
        try:
            report_progress(
                status="failed",
                progress=100.0,
                message=f"Job failed: {e}",
                error=str(e)
            )
        except Exception as callback_exc:
            logger.error("Failed to send failure callback: %s", callback_exc)
        sys.exit(1)

if __name__ == "__main__":
    main()
