"""FastAPI app wiring the local pipeline together.

Startup loads the whisper model once and ensures the bundled font exists. The
heavy pipeline (download -> transcribe -> select -> render) runs on a background
thread as a :class:`~app.jobs.Job` so the request returns instantly; the
frontend then streams live progress over Server-Sent Events. Every domain error
is captured on the job (and surfaced in the stream) so the server never crashes.
"""

from __future__ import annotations

import asyncio
import json
import logging
import subprocess
import sys
import sqlite3
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, File, HTTPException, UploadFile, BackgroundTasks, Header, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import captions, director, fonts, history, jobs, mood, music, prefetch, pretranscribe, transcriber, uploads, db, youtube
from .models import Device, GenerateRequest, InvalidVideoURLError, TranscriptionError
from .paths import CLIPS_DIR, FONTS_DIR, MUSIC_DIR, STATIC_DIR, WEB_DIST_DIR, ROOT_DIR, ensure_dirs

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("ai_video_clipper")


def query_recent_shorts() -> list:
    """Run a custom query against SQLite directly to return the last 100 entries from generated_shorts."""
    db_path = getattr(db, 'DB_PATH', None) or getattr(db, 'db_path', None)
    if not db_path:
        for name in ("app.db", "clipforge.db", "database.db"):
            p = ROOT_DIR / name
            if p.exists():
                db_path = p
                break
        if not db_path:
            db_path = ROOT_DIR / "app.db"

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT * FROM generated_shorts ORDER BY id DESC LIMIT 100")
        rows = cursor.fetchall()
        return [dict(row) for row in rows]
    except Exception as e:
        logger.error(f"Failed to query generated_shorts directly: {e}")
        return []
    finally:
        conn.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Create dirs, ensure the font, load whisper ONCE, and manage background scheduler."""
    ensure_dirs()
    fonts.ensure_fonts()
    transcriber.load_model()
    logger.info(
        "Startup complete. Whisper '%s' on %s. No external AI APIs are used.",
        transcriber.MODEL_SIZE,
        transcriber.get_device(),
    )
    
    # Import and start SchedulerWorker
    from .scheduler import SchedulerWorker
    worker = SchedulerWorker()
    worker.start()
    app.state.scheduler_worker = worker
    logger.info("Background SchedulerWorker started.")
    
    try:
        yield
    finally:
        # On lifespan shutdown, call worker.stop()
        if hasattr(app.state, "scheduler_worker") and app.state.scheduler_worker:
            logger.info("Stopping SchedulerWorker...")
            app.state.scheduler_worker.stop()
            logger.info("SchedulerWorker stopped.")


app = FastAPI(title="Local AI Video Clipper", version="0.1.0", lifespan=lifespan)

# Permissive CORS for local development (frontend served from the same origin).
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serve generated clips so the frontend can preview/download them.
app.mount("/clips", StaticFiles(directory=str(CLIPS_DIR)), name="clips")
# Serve caption fonts so the UI can @font-face them for an accurate live preview.
app.mount("/fonts", StaticFiles(directory=str(FONTS_DIR)), name="fonts")
# Serve the music library so the UI can preview tracks with an <audio> element.
app.mount("/music", StaticFiles(directory=str(MUSIC_DIR)), name="music")
# Serve the built React dashboard's hashed JS/CSS bundles (web/dist/assets) when a
# production build exists, so the backend can serve the React app at "/" directly.
if (WEB_DIST_DIR / "assets").is_dir():
    app.mount("/assets", StaticFiles(directory=str(WEB_DIST_DIR / "assets")), name="assets")


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "device": transcriber.get_device()}


@app.get("/api/caption-styles")
def caption_styles() -> list[dict]:
    """Return the caption style presets for the UI (chips + live preview)."""
    return captions.get_presets_for_api()


@app.get("/api/devices")
def devices() -> dict:
    """Report compute devices the UI may offer for transcription.

    Lets the frontend enable/disable the GPU option and show the active device.
    """
    return {
        "devices": transcriber.available_devices(),
        "default": transcriber.get_device(),
        "cuda_available": transcriber.cuda_available(),
        "gpu_name": transcriber.gpu_name(),
    }


@app.post("/api/warmup")
def warmup(device: Device = Device.AUTO) -> dict:
    """Load the Whisper model on the chosen device and report readiness.

    The frontend calls this when the user changes the Compute dropdown so it can
    show a live "loading / ready / failed" status. Loads are cached per device,
    so re-selecting a warm device returns instantly.
    """
    already = device.value != "auto" and transcriber.is_loaded(device.value)
    try:
        transcriber.load_model(device.value)
        return {
            "status": "ready",
            "device": transcriber.get_device(),
            "cached": already,
        }
    except TranscriptionError as exc:
        return {"status": "error", "device": device.value, "message": str(exc)}


@app.post("/api/upload")
def upload(file: UploadFile = File(...)) -> dict:
    """Accept a video file from the user's machine and return an upload reference.

    The returned ``upload_id`` is then passed to ``POST /api/generate`` instead of
    a ``video_url``. Runs in the threadpool (sync def) so streaming a large file
    to disk doesn't block the event loop.
    """
    try:
        info = uploads.save_upload(file.filename, file.file)
    except InvalidVideoURLError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    finally:
        file.file.close()
    return {"status": "ok", **info}


@app.get("/api/fonts")
def get_fonts() -> dict:
    """List caption fonts the UI can offer (bundled trending set + user uploads)."""
    return fonts.list_fonts()


@app.post("/api/fonts/upload")
def upload_font(file: UploadFile = File(...)) -> dict:
    """Accept a user .ttf/.otf font and register it for use in captions."""
    try:
        info = fonts.save_user_font(file.filename, file.file)
    except InvalidVideoURLError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    finally:
        file.file.close()
    return {"status": "ok", **info}


@app.get("/api/music")
def get_music() -> dict:
    """List background-music tracks (files in assets/music + uploads)."""
    return {"tracks": music.list_tracks()}


@app.post("/api/music/upload")
def upload_music(file: UploadFile = File(...)) -> dict:
    """Accept a user audio file and add it to the background-music library."""
    try:
        info = music.save_track(file.filename, file.file)
    except InvalidVideoURLError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    finally:
        file.file.close()
    return {"status": "ok", **info}


class PrefetchRequest(BaseModel):
    """Body for POST /api/prefetch — start fetching a URL video in the background."""

    video_url: str


@app.post("/api/prefetch")
def prefetch_start(req: PrefetchRequest) -> dict:
    """Begin downloading a URL video ahead of time and return a prefetch id.

    The frontend calls this when the user reaches Step 2 with a pasted link, then
    polls ``GET /api/prefetch/{id}`` for progress. When done, the returned
    ``download_id`` is passed to ``/api/generate`` so the pipeline reuses the file.
    """
    url = (req.video_url or "").strip()
    if not url:
        raise HTTPException(status_code=400, detail="No video URL was provided.")
    pf = prefetch.start(url)
    return {"prefetch_id": pf.id, **pf.snapshot()}


@app.get("/api/prefetch/{prefetch_id}")
def prefetch_status(prefetch_id: str) -> dict:
    """Return the current progress/result of a background prefetch."""
    pf = prefetch.get(prefetch_id)
    if pf is None:
        raise HTTPException(status_code=404, detail="Unknown prefetch id.")
    return pf.snapshot()


class PretranscribeRequest(BaseModel):
    """Body for POST /api/pretranscribe — transcribe a ready video in the background."""

    source_id: str
    device: Device = Device.AUTO
    language: Optional[str] = None


@app.post("/api/pretranscribe")
def pretranscribe_start(req: PretranscribeRequest) -> dict:
    """Begin transcribing an already-downloaded/uploaded video ahead of Generate.

    Called once the video is on disk (download finished, or an upload). The
    result is cached by source id, so the Generate pipeline reuses it and skips
    the transcribe stage. The frontend polls ``GET /api/pretranscribe/{id}``.
    """
    source_id = (req.source_id or "").strip()
    if not source_id:
        raise HTTPException(status_code=400, detail="No source id was provided.")
    job = pretranscribe.start(source_id, req.device.value, req.language)
    return {"pretranscribe_id": job.id, **job.snapshot()}


@app.get("/api/pretranscribe/{job_id}")
def pretranscribe_status(job_id: str) -> dict:
    """Return the current progress/result of a background pre-transcription."""
    job = pretranscribe.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Unknown pretranscribe id.")
    return job.snapshot()


@app.get("/api/download/{download_id}/video")
def download_file(download_id: str) -> FileResponse:
    """Stream a prefetched video file so the Step-2 preview can swap from the
    embeddable player to the real downloaded file once it's ready.

    The id is resolved via the same ``downloads/<id>.*`` glob used for uploads,
    so it can never reach a file outside the downloads directory. FileResponse
    serves Range requests, so the ``<video>`` element can seek/stream normally.
    """
    try:
        path = uploads.resolve_upload(download_id)
    except InvalidVideoURLError:
        raise HTTPException(status_code=404, detail="Downloaded video not found.")
    return FileResponse(str(path))


class ClipRef(BaseModel):
    """Reference to one generated clip."""

    clip_id: str
    index: int


@app.get("/api/history")
def get_history() -> list:
    """All past generations and their clips, newest first (for the Clips panel)."""
    return history.list_entries()


@app.delete("/api/clip/{clip_id}/{index}")
def delete_clip(clip_id: str, index: int) -> dict:
    """Remove one generated clip (deletes the file and drops it from history)."""
    if history.clip_path(clip_id, index) is None:
        raise HTTPException(status_code=400, detail="Invalid clip reference.")
    deleted = history.remove_clip(clip_id, index)
    return {"status": "ok", "deleted": deleted}


@app.post("/api/reveal")
def reveal_clip(ref: ClipRef) -> dict:
    """Open the clip's folder in the OS file manager with the file selected.

    Local-only convenience (this app runs on the user's own machine). The path is
    validated to live under the clips directory before anything is launched.
    """
    path = history.clip_path(ref.clip_id, ref.index)
    if path is None or not path.exists():
        raise HTTPException(status_code=404, detail="Clip not found on disk.")
    try:
        if sys.platform.startswith("win"):
            # explorer returns a non-zero exit code even on success — ignore it.
            subprocess.run(["explorer", "/select,", str(path)], check=False)
        elif sys.platform == "darwin":
            subprocess.run(["open", "-R", str(path)], check=False)
        else:
            subprocess.run(["xdg-open", str(path.parent)], check=False)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"Could not open the folder: {exc}")
    return {"status": "ok"}


@app.get("/")
def index() -> FileResponse:
    """Serve the frontend.

    Prefer the built React dashboard (web/dist) so the backend and the dev server
    show the SAME app; fall back to the legacy vanilla page only if no build exists.
    ``no-store`` keeps the browser from pinning a stale index that points at old,
    since-rebuilt asset hashes (the "old UI until hard refresh" bug).
    """
    react_index = WEB_DIST_DIR / "dashboard.html"
    target = react_index if react_index.is_file() else (STATIC_DIR / "index.html")
    return FileResponse(str(target), headers={"Cache-Control": "no-store"})



@app.post("/api/generate")
def generate(req: GenerateRequest, request: Request) -> dict:
    """Start the pipeline on a background thread (local) or dispatch to GitHub Actions (remote)."""
    import secrets
    import hashlib
    import uuid
    from . import github_dispatch

    run_mode = db.get_setting("run_mode", "local")
    if run_mode == "remote":
        # Check that GitHub integration settings are configured
        github_token = db.get_setting('github_token')
        github_repo = db.get_setting('github_repo')
        if not github_token or not github_repo or '/' not in github_repo:
            raise HTTPException(
                status_code=400,
                detail="GitHub Actions integration settings (github_token, github_repo) are not fully configured."
            )
            
        # Generate a secure random token
        callback_token = secrets.token_hex(16)
        
        # Derive source_video_id
        if req.upload_id:
            source_video_id = req.upload_id
            # Register in source_videos table so DB foreign key doesn't fail
            db.add_source_video(
                video_id=source_video_id,
                url=req.video_url or f"upload://{req.upload_id}",
                channel_url="upload",
                title=req.upload_name or "Uploaded Video",
                status='downloaded'
            )
        elif req.video_url:
            import re
            yt_match = re.search(r'(?:v=|\/shorts\/|\/embed\/|\/v\/|youtu\.be\/|\/watch\?v=|&v=)([^#\&\?]{11})', req.video_url)
            if yt_match:
                source_video_id = yt_match.group(1)
            else:
                source_video_id = hashlib.md5(req.video_url.encode('utf-8')).hexdigest()
            # Register in source_videos table
            db.add_source_video(
                video_id=source_video_id,
                url=req.video_url,
                channel_url="url",
                title="YouTube Video" if yt_match else "Remote URL Video",
                status='new'
            )
        else:
            raise HTTPException(status_code=400, detail="Either upload_id or video_url is required.")
            
        job_id = uuid.uuid4().hex
        
        # Serialize GenerateRequest
        req_json = req.model_dump_json()
        
        # Edit plan JSON
        edit_plan_json = json.dumps(req.edit_plan) if req.edit_plan else None
        
        # Create DB job record
        db.add_job(
            job_id=job_id,
            source_video_id=source_video_id,
            status='PENDING',
            request_json=req_json,
            callback_token=callback_token,
            edit_plan_json=edit_plan_json
        )
        
        # Calculate server_url
        server_url = db.get_setting("public_server_url")
        if not server_url:
            server_url = str(request.base_url).rstrip("/")
            
        # Trigger the GitHub actions workflow
        success, err_msg = github_dispatch.dispatch_workflow(job_id, callback_token, server_url)
        if success:
            logger.info("[%s] remote job dispatched successfully", job_id)
            # Register in-memory job placeholder to support SSE /progress and /result
            job = jobs.Job(req)
            job.id = job_id
            job.status = "queued"
            job.stage = "queued"
            job.message = "Remote job dispatched to GitHub Actions."
            with jobs._JOBS_LOCK:
                jobs._JOBS[job_id] = job
            return {"job_id": job_id}
        else:
            db.update_job_status(job_id, 'FAILED', error=f"GitHub dispatch failed: {err_msg}")
            raise HTTPException(status_code=500, detail=f"Failed to trigger the GitHub Actions workflow: {err_msg}")

    else:
        # local run: run the legacy background thread runner
        job = jobs.create_job(req)
        jobs.start_job(job)
        logger.info("[%s] local job accepted", job.id)
        return {"job_id": job.id}


@app.get("/api/music-suggest/{source_id}")
def music_suggest(source_id: str, language: Optional[str] = None) -> dict:
    """Suggest a music mood (sad/happy/romantic/…) from the prepared transcript."""
    tr = pretranscribe.cached(source_id, language) or pretranscribe.cached(source_id, None)
    if not tr:
        return {"ready": False}
    segs = tr.get("segments") or []
    text = " ".join((s.get("text") or "") for s in segs)
    return {"ready": True, **mood.suggest_mood(text)}


@app.post("/api/cancel/{job_id}")
def cancel(job_id: str) -> dict:
    """Flag a running job for cancellation; it stops at the next clip boundary."""
    job = jobs.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Unknown job id.")
    job.cancel()
    logger.info("[%s] cancel requested", job_id)
    return {"status": "cancelled"}


@app.get("/api/progress/{job_id}")
async def progress(job_id: str) -> StreamingResponse:
    """Stream a job's progress as Server-Sent Events until it finishes."""
    job = jobs.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Unknown job id.")

    async def event_stream():
        last_rev = -1
        while True:
            snap = job.snapshot()
            if snap["rev"] != last_rev:
                last_rev = snap["rev"]
                yield f"data: {json.dumps(snap)}\n\n"
            if snap["status"] in ("done", "error"):
                break
            await asyncio.sleep(0.3)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/result/{job_id}")
def result(job_id: str) -> dict:
    """Return the current snapshot of a job (useful after a stream reconnect)."""
    job = jobs.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Unknown job id.")
    return job.snapshot()


class KeySettingsRequest(BaseModel):
    groq_api_key: Optional[str] = None


@app.get("/api/settings/keys")
def get_key_settings() -> dict:
    import os
    key = os.environ.get("GROQ_API_KEY", "")
    masked = f"{key[:4]}...{key[-4:]}" if len(key) >= 8 else ("Set" if key else "Not Set")
    return {"groq_api_key_set": bool(key), "masked_key": masked}


@app.post("/api/settings/keys")
def set_key_settings(req: KeySettingsRequest) -> dict:
    import os
    if req.groq_api_key is not None:
        os.environ["GROQ_API_KEY"] = req.groq_api_key.strip()
    return {"status": "ok", "groq_api_key_set": bool(os.environ.get("GROQ_API_KEY"))}


# --- NEW FACTORY ENDPOINTS ---

class AddChannelRequest(BaseModel):
    url: str
    name: Optional[str] = None


class UpdateSettingsRequest(BaseModel):
    shorts_per_day: Optional[int] = None
    buffer_days: Optional[int] = None
    youtube_client_id: Optional[str] = None
    youtube_client_secret: Optional[str] = None
    transcribe_device: Optional[str] = None
    storage_provider: Optional[str] = None
    s3_endpoint_url: Optional[str] = None
    s3_access_key: Optional[str] = None
    s3_secret_key: Optional[str] = None
    s3_bucket_name: Optional[str] = None
    s3_region: Optional[str] = None
    s3_public_url_prefix: Optional[str] = None
    github_token: Optional[str] = None
    github_repo: Optional[str] = None
    github_ref: Optional[str] = None
    github_workflow: Optional[str] = None
    run_mode: Optional[str] = None
    public_server_url: Optional[str] = None



@app.get("/api/youtube/auth")
def get_youtube_auth(redirect_uri: str) -> dict:
    try:
        auth_url = youtube.get_auth_url(redirect_uri)
        return {"auth_url": auth_url}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/youtube/callback")
def get_youtube_callback(code: str, redirect_uri: str) -> dict:
    try:
        youtube.save_oauth_callback(code, redirect_uri)
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/channels")
def get_channels() -> list:
    try:
        return db.get_source_channels()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/channels")
def add_channel(req: AddChannelRequest) -> dict:
    try:
        try:
            channel = db.add_source_channel(url=req.url, name=req.name)
        except TypeError:
            channel = db.add_source_channel(req.url, req.name)
        return {"status": "success", "channel": channel}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/channels/{channel_id}")
def delete_channel(channel_id: str) -> dict:
    try:
        db.remove_source_channel(channel_id)
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/settings")
def get_settings() -> dict:
    try:
        shorts_per_day_str = db.get_setting("shorts_per_day")
        buffer_days_str = db.get_setting("buffer_days")
        
        shorts_per_day = int(shorts_per_day_str) if shorts_per_day_str is not None else 3
        buffer_days = int(buffer_days_str) if buffer_days_str is not None else 2
        
        youtube_client_id = db.get_setting("youtube_client_id") or ""
        youtube_client_secret = db.get_setting("youtube_client_secret") or ""
        transcribe_device = db.get_setting("transcribe_device") or "cpu"
        
        youtube_oauth_token = db.get_setting("youtube_oauth_token")
        youtube_linked = bool(youtube_oauth_token)
        
        return {
            "shorts_per_day": shorts_per_day,
            "buffer_days": buffer_days,
            "youtube_client_id": youtube_client_id,
            "youtube_client_secret": youtube_client_secret,
            "transcribe_device": transcribe_device,
            "youtube_linked": youtube_linked,
            # S3 settings
            "storage_provider": db.get_setting("storage_provider") or "local",
            "s3_endpoint_url": db.get_setting("s3_endpoint_url") or "",
            "s3_access_key": db.get_setting("s3_access_key") or "",
            "s3_secret_key": db.get_setting("s3_secret_key") or "",
            "s3_bucket_name": db.get_setting("s3_bucket_name") or "",
            "s3_region": db.get_setting("s3_region") or "",
            "s3_public_url_prefix": db.get_setting("s3_public_url_prefix") or "",
            # GitHub settings
            "github_token": db.get_setting("github_token") or "",
            "github_repo": db.get_setting("github_repo") or "",
            "github_ref": db.get_setting("github_ref") or "main",
            "github_workflow": db.get_setting("github_workflow") or "render.yml",
            "run_mode": db.get_setting("run_mode") or "local",
            "public_server_url": db.get_setting("public_server_url") or ""
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/settings")
def update_settings(req: UpdateSettingsRequest) -> dict:
    try:
        set_fn = getattr(db, "set_setting", None) or getattr(db, "save_setting", None)
        if not set_fn:
            raise ValueError("No setting saving function found in database module.")
            
        if req.shorts_per_day is not None:
            set_fn("shorts_per_day", req.shorts_per_day)
        if req.buffer_days is not None:
            set_fn("buffer_days", req.buffer_days)
        if req.youtube_client_id is not None:
            set_fn("youtube_client_id", req.youtube_client_id)
        if req.youtube_client_secret is not None:
            set_fn("youtube_client_secret", req.youtube_client_secret)
        if req.transcribe_device is not None:
            set_fn("transcribe_device", req.transcribe_device)
            
        if req.storage_provider is not None:
            set_fn("storage_provider", req.storage_provider)
        if req.s3_endpoint_url is not None:
            set_fn("s3_endpoint_url", req.s3_endpoint_url)
        if req.s3_access_key is not None:
            set_fn("s3_access_key", req.s3_access_key)
        if req.s3_secret_key is not None:
            set_fn("s3_secret_key", req.s3_secret_key)
        if req.s3_bucket_name is not None:
            set_fn("s3_bucket_name", req.s3_bucket_name)
        if req.s3_region is not None:
            set_fn("s3_region", req.s3_region)
        if req.s3_public_url_prefix is not None:
            set_fn("s3_public_url_prefix", req.s3_public_url_prefix)
            
        if req.github_token is not None:
            set_fn("github_token", req.github_token)
        if req.github_repo is not None:
            set_fn("github_repo", req.github_repo)
        if req.github_ref is not None:
            set_fn("github_ref", req.github_ref)
        if req.github_workflow is not None:
            set_fn("github_workflow", req.github_workflow)
        if req.run_mode is not None:
            set_fn("run_mode", req.run_mode)
        if req.public_server_url is not None:
            set_fn("public_server_url", req.public_server_url)
            
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))



@app.post("/api/automation/start")
def start_automation(request: Request, background_tasks: BackgroundTasks) -> dict:
    try:
        db.set_setting("automation_status", "RUNNING")
        worker = getattr(request.app.state, "scheduler_worker", None)
        if worker:
            background_tasks.add_task(worker.trigger_once)
        return {"status": "success", "automation_status": "RUNNING"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/automation/cancel")
def cancel_automation(request: Request) -> dict:
    try:
        db.set_setting("automation_status", "CANCELLED")
        db.cancel_all_active_jobs()
        return {"status": "success", "automation_status": "CANCELLED"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/automation/status")
def get_automation_status(request: Request) -> dict:
    try:
        status = db.get_setting("automation_status", "IDLE")
        last_job = db.get_last_job_info()
        last_published = db.get_last_published_short()
        
        return {
            "status": status,
            "current_job_id": last_job.get("id") if last_job else None,
            "current_job_status": last_job.get("status") if last_job else None,
            "current_job_progress": last_job.get("progress", 0) if last_job else 0,
            "current_job_error": last_job.get("error") if last_job else None,
            "last_published_title": last_published.get("title") if last_published else None,
            "last_published_time": last_published.get("created_at") if last_published else None,
            "last_published_youtube_id": last_published.get("youtube_video_id") if last_published else None,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/storage/disk-usage")
def get_disk_usage() -> dict:
    try:
        import shutil
        total, used, free = shutil.disk_usage("/")
        return {
            "total_gb": round(total / (1024**3), 2),
            "used_gb": round(used / (1024**3), 2),
            "free_gb": round(free / (1024**3), 2),
            "used_percent": round((used / total) * 100, 2)
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/shorts")


def get_shorts(status: Optional[str] = None) -> list:
    try:
        if status:
            return db.get_shorts_by_status(status)
        return query_recent_shorts()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/shorts/upload/{short_id}")
def upload_short(short_id: str, background_tasks: BackgroundTasks) -> dict:
    try:
        background_tasks.add_task(youtube.upload_scheduled_short, short_id)
        return {"status": "success", "message": "Manual upload triggered in background."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/jobs")
def get_jobs() -> list:
    try:
        return db.list_jobs()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class JobCallbackRequest(BaseModel):
    status: Optional[str] = None
    progress: Optional[float] = None
    message: Optional[str] = None
    error: Optional[str] = None
    s3_url: Optional[str] = None
    gha_run_id: Optional[str] = None
    clips: Optional[List[dict]] = None
    seo_metadata: Optional[dict] = None


@app.get("/api/jobs/{job_id}/config")
def get_job_config(
    job_id: str,
    authorization: Optional[str] = Header(None),
    token: Optional[str] = Query(None)
) -> dict:
    req_token = None
    if authorization and authorization.lower().startswith("bearer "):
        req_token = authorization[7:].strip()
    elif token:
        req_token = token.strip()
        
    if not req_token:
        raise HTTPException(status_code=401, detail="Missing authentication token.")
        
    job_row = db.get_job(job_id)
    if not job_row:
        raise HTTPException(status_code=404, detail="Job not found.")
        
    db_token = job_row.get('callback_token')
    if not db_token or req_token != db_token:
        raise HTTPException(status_code=403, detail="Invalid callback token.")
        
    s3_settings = {
        "storage_provider": db.get_setting("storage_provider") or "local",
        "s3_endpoint_url": db.get_setting("s3_endpoint_url") or "",
        "s3_access_key": db.get_setting("s3_access_key") or "",
        "s3_secret_key": db.get_setting("s3_secret_key") or "",
        "s3_bucket_name": db.get_setting("s3_bucket_name") or "",
        "s3_region": db.get_setting("s3_region") or "",
        "s3_public_url_prefix": db.get_setting("s3_public_url_prefix") or "",
    }
    
    import os
    groq_api_key = os.environ.get("GROQ_API_KEY", "")
    
    edit_plan = None
    ep_json = job_row.get('edit_plan_json')
    if ep_json:
        try:
            edit_plan = json.loads(ep_json)
        except Exception:
            pass
            
    source_video = {
        "video_url": job_row.get('video_url'),
        "video_title": job_row.get('video_title'),
        "source_video_id": job_row.get('source_video_id'),
    }
    
    req_payload = None
    req_json = job_row.get('request_json')
    if req_json:
        try:
            req_payload = json.loads(req_json)
        except Exception:
            pass
            
    return {
        "s3_settings": s3_settings,
        "groq_api_key": groq_api_key,
        "edit_plan": edit_plan,
        "source_video": source_video,
        "req": req_payload,
        "request_payload": req_payload
    }


@app.post("/api/jobs/{job_id}/callback")
@app.patch("/api/jobs/{job_id}/callback")
def job_callback(
    job_id: str,
    req: JobCallbackRequest,
    authorization: Optional[str] = Header(None),
    token: Optional[str] = Query(None)
) -> dict:
    req_token = None
    if authorization and authorization.lower().startswith("bearer "):
        req_token = authorization[7:].strip()
    elif token:
        req_token = token.strip()
        
    if not req_token:
        raise HTTPException(status_code=401, detail="Missing authentication token.")
        
    job_row = db.get_job(job_id)
    if not job_row:
        raise HTTPException(status_code=404, detail="Job not found.")
        
    db_token = job_row.get('callback_token')
    if not db_token or req_token != db_token:
        raise HTTPException(status_code=403, detail="Invalid callback token.")
        
    try:
        db.update_job_from_callback(
            job_id=job_id,
            status=req.status,
            progress=req.progress,
            error=req.error,
            s3_url=req.s3_url,
            gha_run_id=req.gha_run_id,
            seo_metadata=req.seo_metadata,
            clips=req.clips
        )
    except Exception as e:
        logger.exception("Failed to update database from job callback: %s", e)
        raise HTTPException(status_code=500, detail=f"Database update failed: {e}")
        
    with jobs._JOBS_LOCK:
        job = jobs._JOBS.get(job_id)
        
    if job:
        with job._lock:
            if req.status is not None:
                db_status_lower = req.status.lower()
                if db_status_lower in ("queued", "pending"):
                    job.status = "queued"
                    job.stage = "queued"
                elif db_status_lower in ("done", "ready", "scheduled", "published"):
                    job.status = "done"
                    job.stage = "done"
                elif db_status_lower in ("error", "failed"):
                    job.status = "error"
                    job.stage = "error"
                else:
                    job.status = "running"
                    job.stage = db_status_lower
            
            if req.progress is not None:
                job.progress = float(req.progress) / 100.0
                job.stage_progress = float(req.progress) / 100.0
                
            if req.message is not None:
                job.message = req.message
            elif req.status is not None:
                job.message = f"Status: {req.status}"
                
            if req.error is not None:
                job.error = req.error
                job.message = req.error
                
            if req.clips is not None:
                mapped_clips = []
                for c in req.clips:
                    idx = c.get('index') if c.get('index') is not None else c.get('clip_index', 0)
                    title = c.get('title') or f"Clip {idx + 1}"
                    start = c.get('start') or 0.0
                    end = c.get('end') or 0.0
                    if not start and not end:
                        ep_val = c.get('edit_plan') or c.get('edit_plan_json')
                        if ep_val:
                            try:
                                if isinstance(ep_val, str):
                                    ep_val = json.loads(ep_val)
                                if 'cuts' in ep_val and ep_val['cuts']:
                                    start = ep_val['cuts'][0].get('start_time', 0.0)
                                    duration = sum(float(x.get('end_time', 0.0)) - float(x.get('start_time', 0.0)) for x in ep_val['cuts'])
                                    end = start + duration
                            except Exception:
                                pass
                    
                    clip_id = c.get('id') or c.get('clip_id') or c.get('short_id') or f"shorts_{job_id}_{idx}"
                    url = c.get('url') or c.get('file_path') or f"/clips/{clip_id}/{idx}.mp4"
                    mapped_clips.append({
                        "index": idx,
                        "title": title,
                        "start": round(start, 2),
                        "end": round(end, 2),
                        "url": url,
                        "language": job.req.language or "en",
                        "filename": jobs.download_filename(title, job.req.language, idx)
                    })
                job.clips = mapped_clips
                
            job._rev += 1
            
    return {"status": "success"}




# ---------------------------------------------------------------------------
# AI-Assisted Editing Endpoint
# Accepts a natural-language editing prompt, routes it through OmniRoute,
# and returns the structured EditPlan JSON that the frontend can preview
# before the user kicks off a full render via /api/generate.
# ---------------------------------------------------------------------------

class AIEditRequest(BaseModel):
    """Body for POST /api/ai-edit."""
    editing_prompt: str
    transcript: dict  # Raw Whisper output dict (must have a 'words' key)
    video_title: str = "Untitled"


@app.post("/api/ai-edit")
def ai_edit(req: AIEditRequest) -> dict:
    """
    Translate a natural-language editing prompt into a structured EditPlan.

    The EditPlan is returned as JSON and can be passed directly to
    POST /api/generate as the ``edit_plan`` field.

    Pipeline:
      USER PROMPT -> OmniRoute -> AI Editing Director -> EditPlan (JSON)

    Note: This endpoint only *generates* the plan; video rendering happens
    when the user confirms and calls /api/generate with the returned edit_plan.
    """
    try:
        plan = director.generate_edit_plan_from_prompt(
            user_prompt=req.editing_prompt,
            transcript=req.transcript,
            video_title=req.video_title,
        )
        return {
            "status": "success",
            "edit_plan": plan.model_dump(),
        }
    except Exception as exc:
        logger.error("ai_edit failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))

# Catch-all guard: turn any unexpected error into a clean 500 (no crash).
@app.exception_handler(Exception)
async def unhandled_exception_handler(request, exc):  # noqa: ANN001
    logger.exception("Unhandled error on %s", request.url.path)
    return JSONResponse(
        status_code=500,
        content={"detail": f"Unexpected server error: {exc}"},
    )

# Mount web/dist static files at root so style.css, app.js, and other static assets serve correctly.
if WEB_DIST_DIR.is_dir():
    app.mount("/", StaticFiles(directory=str(WEB_DIST_DIR), html=True), name="dist_root")
elif STATIC_DIR.is_dir():
    app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static_root")
