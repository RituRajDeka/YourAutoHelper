#!/usr/bin/env python3
"""Standalone local daemon worker script.

This script runs in a loop on the local WSL instance. It polls the Railway server
for pending downloader jobs, downloads the raw source video using yt-dlp (which
works perfectly on residential local IPs), uploads the downloaded file to S3,
and fires callbacks to update status.
"""

import sys
import os
import time
import argparse
import logging
import requests
import re
import hashlib
import boto3
import mimetypes
from pathlib import Path
from typing import Optional, List, Dict, Any
from botocore.client import Config
from botocore.exceptions import ClientError

# Ensure parent directory is in sys.path so we can import from app
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    from app import downloader
    from app.paths import DOWNLOADS_DIR
except ImportError:
    # Fallback paths setup if imported differently
    DOWNLOADS_DIR = Path("downloads")

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("local_agent")


def mask_url(url: str) -> str:
    """Mask tokens in URLs to prevent sensitive data logging."""
    if not url:
        return ""
    # Mask token parameter
    url = re.sub(r'token=[^&]+', 'token=***', url)
    # Mask Authorization header style values if logged
    url = re.sub(r'Bearer\s+[^&]+', 'Bearer ***', url)
    return url


def extract_video_id(url: str) -> str:
    """Extract YouTube video ID or return a hash of the URL."""
    match = re.search(r'(?:v=|\/shorts\/|\/embed\/|\/v\/|youtu\.be\/|\/watch\?v=|&v=)([^#\&\?]{11})', url)
    if match:
        return match.group(1)
    # Fallback to md5 hash of URL
    return hashlib.md5(url.encode('utf-8')).hexdigest()


def estimate_video_size(url: str) -> Optional[int]:
    """Retrieve estimated video size in bytes using yt-dlp without downloading."""
    try:
        import yt_dlp
        ydl_opts = {
            'format': 'bv*[height<=1080]+ba/b[height<=1080]/b',
            'quiet': True,
            'no_warnings': True,
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            logger.info("Extracting metadata for size estimation from URL: %s", mask_url(url))
            info = ydl.extract_info(url, download=False)
            
            # 1. Try to get total size directly
            filesize = info.get('filesize') or info.get('filesize_approx')
            if filesize:
                return filesize
            
            # 2. Try to get sizes of requested formats
            req_formats = info.get('requested_formats')
            if req_formats:
                filesize = sum(f.get('filesize') or f.get('filesize_approx') or 0 for f in req_formats)
                if filesize > 0:
                    return filesize
            
            # 3. Guess based on duration if present (approx 0.5 MB/sec for 1080p)
            duration = info.get('duration') or 0
            if duration > 0:
                return int(duration * 1024 * 1024 * 0.5)
                
    except Exception as e:
        logger.warning("Could not estimate video size using yt-dlp: %s", e)
    return None


def report_progress(
    server_url: str,
    job_id: str,
    callback_token: str,
    status: str,
    progress: float,
    message: str,
    error: Optional[str] = None,
    s3_url: Optional[str] = None
) -> None:
    """Send job callback to the Railway server."""
    actual_server_url = os.environ.get("CLIPFORGE_CALLBACK_SERVER_URL") or server_url
    url = f"{actual_server_url}/api/jobs/{job_id}/callback?token={callback_token}"
    payload = {
        "status": status,
        "progress": round(progress, 2),
        "message": message,
        "error": error,
        "s3_url": s3_url
    }
    headers = {
        "Authorization": f"Bearer {callback_token}",
        "Content-Type": "application/json"
    }
    logger.info("Sending callback: status=%s, progress=%.1f%%, message=%s", status, progress, message)
    
    # Try PATCH first, fallback to POST
    try:
        resp = requests.patch(url, headers=headers, json=payload, timeout=15)
        resp.raise_for_status()
    except Exception as patch_err:
        logger.warning("PATCH callback failed, trying POST: %s", patch_err)
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=15)
            resp.raise_for_status()
        except Exception as post_err:
            logger.error("Callback POST also failed: %s", post_err)


def process_job(server_url: str, token: str, job: dict) -> None:
    """Process a single downloader job."""
    job_id = job.get("job_id") or job.get("id")
    callback_token = job.get("callback_token")
    request_json_raw = job.get("request_json")

    if not job_id or not callback_token:
        logger.error("Job is missing 'job_id' or 'callback_token'. Skipping: %s", job)
        return

    logger.info("Discovered pending downloader job %s...", job_id)
    
    # Parse GenerateRequest dictionary
    request_json = {}
    if request_json_raw:
        if isinstance(request_json_raw, str):
            try:
                request_json = requests.utils.json.loads(request_json_raw)
            except Exception as e:
                logger.error("Failed to parse request_json string: %s", e)
        elif isinstance(request_json_raw, dict):
            request_json = request_json_raw

    # Initialize variables for source video
    video_url = None
    video_title = "Source Video"
    
    # Handle candidates scoring if multiple candidates exist
    candidates = request_json.get("candidates", [])
    if candidates and len(candidates) > 1:
        logger.info("Job %s contains %d candidate video URLs. Scoring candidates...", job_id, len(candidates))
        try:
            formatted_candidates = []
            for c in candidates:
                formatted_candidates.append({
                    "url": c.get("url") or c.get("Url"),
                    "title": c.get("title") or c.get("Title") or "Candidate Video",
                    "views": c.get("views") or c.get("Views") or 0,
                    "likes": c.get("likes") or c.get("Likes") or 0,
                    "duration": c.get("duration") or c.get("Duration") or 0
                })
            
            score_url = f"{server_url}/api/jobs/score-candidates?token={token}"
            score_resp = requests.post(
                score_url,
                json={"token": token, "candidates": formatted_candidates},
                headers={"Authorization": f"Bearer {token}"},
                timeout=15
            )
            score_resp.raise_for_status()
            scored_data = score_resp.json()
            
            # Scored response extraction
            if isinstance(scored_data, dict):
                scored_list = scored_data.get("candidates", [])
            else:
                scored_list = scored_data
                
            if scored_list:
                # Sort by score descending if available
                if isinstance(scored_list[0], dict) and 'score' in scored_list[0]:
                    scored_list = sorted(scored_list, key=lambda x: x.get('score', 0), reverse=True)
                
                top_candidate = scored_list[0]
                video_url = top_candidate.get("url")
                video_title = top_candidate.get("title") or top_candidate.get("Title") or "Scored Source Video"
                logger.info("Top ranked candidate selected: URL=%s, Title=%s", mask_url(video_url), video_title)
        except Exception as score_err:
            logger.exception("Failed to score candidates: %s", score_err)
            # Fallback to the first candidate URL
            if candidates:
                video_url = candidates[0].get("url") or candidates[0].get("Url")
                video_title = candidates[0].get("title") or candidates[0].get("Title") or "Fallback Candidate Video"

    # Single URL fallback / extract main url
    if not video_url:
        if candidates:
            video_url = candidates[0].get("url") or candidates[0].get("Url")
            video_title = candidates[0].get("title") or candidates[0].get("Title") or "Fallback Candidate Video"
        if not video_url:
            video_url = request_json.get("video_url")
            video_title = request_json.get("video_title") or "Source Video"

    if not video_url or not video_url.strip():
        logger.error("No valid video URL or candidates found for job %s.", job_id)
        report_progress(
            server_url=server_url,
            job_id=job_id,
            callback_token=callback_token,
            status="FAILED",
            progress=100.0,
            message="No video URL provided.",
            error="INVALID_URL"
        )
        return

    logger.info("Selected target video URL: %s", mask_url(video_url))
    video_id = extract_video_id(video_url)
    remote_name = f"sources/{video_id}.mp4"

    # Step 1: Fetch job configuration to get S3 settings
    logger.info("Fetching job configuration for job %s...", job_id)
    try:
        config_url = f"{server_url}/api/jobs/{job_id}/config?token={callback_token}"
        config_resp = requests.get(config_url, headers={"Authorization": f"Bearer {callback_token}"}, timeout=15)
        config_resp.raise_for_status()
        config_data = config_resp.json()
    except Exception as config_err:
        logger.exception("Failed to fetch job configurations: %s", config_err)
        report_progress(
            server_url=server_url,
            job_id=job_id,
            callback_token=callback_token,
            status="FAILED",
            progress=100.0,
            message=f"Failed to fetch job configuration: {config_err}",
            error="CONFIG_FETCH_FAILED"
        )
        return

    s3_settings = config_data.get("s3_settings", {})
    downloader_mode = config_data.get("downloader_mode", "yt-dlp")
    youtube_cookies = config_data.get("youtube_cookies")
    
    # Store YouTube cookies if available
    if youtube_cookies:
        cookies_str = youtube_cookies.strip()
        if not cookies_str.startswith("# Netscape HTTP Cookie File") and not cookies_str.startswith("# HTTP Cookie File"):
            cookies_str = "# Netscape HTTP Cookie File\n" + cookies_str
            
        cookies_path = DOWNLOADS_DIR / "cookies.txt"
        DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)
        try:
            with open(cookies_path, "w", encoding="utf-8") as f:
                f.write(cookies_str)
            os.environ["CLIPFORGE_COOKIES_FILE"] = str(cookies_path)
            logger.info("YouTube cookies saved to CLIPFORGE_COOKIES_FILE environment variable.")
        except Exception as cookie_err:
            logger.warning("Could not save YouTube cookies to disk: %s", cookie_err)

    # Initialize boto3 S3 Client using credentials from config
    bucket_name = s3_settings.get("s3_bucket_name")
    if not bucket_name:
        logger.error("s3_bucket_name setting is empty in job config.")
        report_progress(
            server_url=server_url,
            job_id=job_id,
            callback_token=callback_token,
            status="FAILED",
            progress=100.0,
            message="S3 bucket name is missing from configurations.",
            error="S3_CONFIG_ERROR"
        )
        return

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
        s3_client_kwargs['config'] = Config(signature_version='s3v4')

    try:
        s3_client = boto3.client('s3', **s3_client_kwargs)
    except Exception as s3_init_err:
        logger.exception("Failed to initialize boto3 client: %s", s3_init_err)
        report_progress(
            server_url=server_url,
            job_id=job_id,
            callback_token=callback_token,
            status="FAILED",
            progress=100.0,
            message=f"Failed to initialize S3 Client: {s3_init_err}",
            error="S3_CLIENT_INIT_FAILED"
        )
        return

    # Check S3 Public URL prefix or build S3 URL
    public_url_prefix = s3_settings.get("s3_public_url_prefix")
    if public_url_prefix:
        s3_url = f"{public_url_prefix.rstrip('/')}/{remote_name.lstrip('/')}"
    else:
        # Construct standard S3 URI or fallback HTTP URL
        try:
            endpoint = s3_client.meta.endpoint_url.rstrip('/')
            s3_url = f"{endpoint}/{bucket_name}/{remote_name.lstrip('/')}"
        except Exception:
            s3_url = f"s3://{bucket_name}/{remote_name}"

    # Step 2: Verify if the video already exists in S3 (de-duplication)
    logger.info("Verifying if video %s already exists in S3 bucket %s...", video_id, bucket_name)
    already_exists = False
    try:
        s3_client.head_object(Bucket=bucket_name, Key=remote_name)
        already_exists = True
        logger.info("Video %s already exists in S3! Skipping download & upload stages.", video_id)
        
        # Fire callback immediately
        report_progress(
            server_url=server_url,
            job_id=job_id,
            callback_token=callback_token,
            status="DOWNLOADED",
            progress=100.0,
            message="Source video already exists in S3 (de-duplicated).",
            s3_url=s3_url
        )
        return
    except ClientError as ce:
        if ce.response['Error']['Code'] == '404':
            already_exists = False
            logger.info("Video %s not found in S3. Proceeding with download.", video_id)
        else:
            logger.warning("Could not perform head_object check on S3: %s. Assuming not exists.", ce)

    # Step 3: Check database storage allocation quota
    logger.info("Checking database storage allocation quota...")
    try:
        quota_url = f"{server_url}/api/storage/quota?token={token}"
        quota_resp = requests.get(quota_url, headers={"Authorization": f"Bearer {token}"}, timeout=15)
        quota_resp.raise_for_status()
        quota_data = quota_resp.json()
        
        # Determine available storage GB
        available_gb = quota_data.get("available_gb")
        limit_gb = config_data.get("video_storage_limit") or config_data.get("settings", {}).get("video_storage_limit")
        if limit_gb is None:
            limit_gb = quota_data.get("limit_gb", 15.0)
            
        used_gb = quota_data.get("used_gb", 0.0)
        # Recalculate remaining space in case limit is different
        remaining_space_gb = max(0.0, limit_gb - used_gb)
        if available_gb is None or available_gb > remaining_space_gb:
            available_gb = remaining_space_gb

        # Check estimated video size
        video_size_bytes = estimate_video_size(video_url)
        if video_size_bytes:
            video_size_gb = video_size_bytes / (1024**3)
            logger.info("Pre-download check: Estimated size: %.3f GB. Available space: %.3f GB.", video_size_gb, available_gb)
            if video_size_gb > available_gb:
                logger.error("Estimated video size %.3f GB exceeds remaining available quota %.3f GB", video_size_gb, available_gb)
                report_progress(
                    server_url=server_url,
                    job_id=job_id,
                    callback_token=callback_token,
                    status="FAILED",
                    progress=100.0,
                    message=f"Storage limit exceeded. Estimated size {video_size_gb:.2f} GB > Available {available_gb:.2f} GB.",
                    error="STORAGE_LIMIT"
                )
                return
    except Exception as quota_err:
        logger.warning("Storage quota check failed or bypassed: %s. Proceeding with caution.", quota_err)
        available_gb = 15.0  # Safe default assumption

    # Step 4: Report DOWNLOADING status and start downloading
    report_progress(
        server_url=server_url,
        job_id=job_id,
        callback_token=callback_token,
        status="DOWNLOADING",
        progress=15.0,
        message="Downloading source video locally..."
    )

    source_path = None
    try:
        # Use our robust local package downloader
        logger.info("Starting yt-dlp download for URL: %s", mask_url(video_url))
        source_path = downloader.download_video(video_url, downloader_mode=downloader_mode)
        
        if not source_path or not source_path.exists():
            raise FileNotFoundError("Downloader did not produce an output file.")

        actual_size_bytes = source_path.stat().st_size
        actual_size_gb = actual_size_bytes / (1024**3)
        logger.info("Download completed successfully. Local path: %s, Size: %.3f GB", source_path, actual_size_gb)

        # Post-download actual storage verification check
        if actual_size_gb > available_gb:
            logger.error("Actual downloaded size %.3f GB exceeds available space %.3f GB", actual_size_gb, available_gb)
            if source_path.exists():
                source_path.unlink()
            report_progress(
                server_url=server_url,
                job_id=job_id,
                callback_token=callback_token,
                status="FAILED",
                progress=100.0,
                message=f"Storage limit exceeded. Downloaded size {actual_size_gb:.2f} GB > Available {available_gb:.2f} GB.",
                error="STORAGE_LIMIT"
            )
            return

    except Exception as dl_err:
        logger.exception("Local download failed: %s", dl_err)
        report_progress(
            server_url=server_url,
            job_id=job_id,
            callback_token=callback_token,
            status="FAILED",
            progress=100.0,
            message=f"Download failed: {dl_err}",
            error=str(dl_err)
        )
        return

    # Step 5: Report UPLOADING status and upload to S3
    report_progress(
        server_url=server_url,
        job_id=job_id,
        callback_token=callback_token,
        status="UPLOADING",
        progress=60.0,
        message="Staging video to S3 storage..."
    )

    try:
        content_type, _ = mimetypes.guess_type(str(source_path))
        extra_args = {}
        if content_type:
            extra_args['ContentType'] = content_type

        logger.info("Uploading %s to S3 bucket %s key %s...", source_path.name, bucket_name, remote_name)
        
        # Try uploading with public-read permission first (standard client default)
        try:
            extra_args['ACL'] = 'public-read'
            s3_client.upload_file(
                Filename=str(source_path),
                Bucket=bucket_name,
                Key=remote_name,
                ExtraArgs=extra_args
            )
        except Exception as acl_exc:
            logger.warning("Uploading with public-read ACL failed (bucket policies might block it): %s. Retrying without ACL...", acl_exc)
            extra_args.pop('ACL', None)
            s3_client.upload_file(
                Filename=str(source_path),
                Bucket=bucket_name,
                Key=remote_name,
                ExtraArgs=extra_args
            )

        # Verify upload succeeds via head_object
        logger.info("Verifying upload of key %s on S3...", remote_name)
        s3_client.head_object(Bucket=bucket_name, Key=remote_name)
        logger.info("S3 upload verified successfully!")

    except Exception as upload_err:
        logger.exception("S3 upload failed: %s", upload_err)
        report_progress(
            server_url=server_url,
            job_id=job_id,
            callback_token=callback_token,
            status="FAILED",
            progress=100.0,
            message=f"S3 Upload failed: {upload_err}",
            error=str(upload_err)
        )
        # Clean up local file even on failure
        if source_path and source_path.exists():
            source_path.unlink()
        return

    # Clean up the local download file to save disk space
    try:
        if source_path and source_path.exists():
            source_path.unlink()
            logger.info("Cleaned up local download file: %s", source_path)
    except Exception as clean_err:
        logger.warning("Could not clean up local file %s: %s", source_path, clean_err)

    # Step 6: Callback success
    report_progress(
        server_url=server_url,
        job_id=job_id,
        callback_token=callback_token,
        status="DOWNLOADED",
        progress=100.0,
        message="Source video uploaded to S3.",
        s3_url=s3_url
    )
    logger.info("Job %s completed successfully! Source video URL staged at S3: %s", job_id, s3_url)


def main() -> None:
    parser = argparse.ArgumentParser(description="Standalone Local Daemon Worker for ClipForge")
    parser.add_argument("--server-url", default="http://localhost:8000", help="Railway server URL")
    parser.add_argument("--token", required=True, help="GitHub token (github_token)")
    parser.add_argument("--poll-interval", type=int, default=5, help="Polling interval in seconds")
    
    # Allow overriding via env vars
    server_url_env = os.environ.get("CLIPFORGE_SERVER_URL")
    token_env = os.environ.get("CLIPFORGE_GITHUB_TOKEN")
    
    args = parser.parse_args()
    
    server_url = server_url_env or args.server_url
    token = token_env or args.token
    poll_interval = args.poll_interval
    
    server_url = server_url.rstrip("/")
    
    if not token:
        logger.critical("GitHub Token is required. Pass --token or set CLIPFORGE_GITHUB_TOKEN.")
        sys.exit(1)
        
    logger.info("ClipForge local agent daemon started. Server: %s, Poll interval: %d seconds", mask_url(server_url), poll_interval)
    
    # Main polling loop
    while True:
        try:
            logger.debug("Polling for pending downloader jobs...")
            url = f"{server_url}/api/jobs/pending?token={token}"
            
            # Perform query
            resp = requests.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=15)
            resp.raise_for_status()
            pending_jobs = resp.json()
            
            if pending_jobs:
                if isinstance(pending_jobs, list):
                    logger.info("Found %d pending job(s) from server.", len(pending_jobs))
                    for job in pending_jobs:
                        try:
                            process_job(server_url, token, job)
                        except Exception as job_err:
                            logger.exception("Error processing individual job: %s", job_err)
                elif isinstance(pending_jobs, dict):
                    # Handle single job return if applicable
                    logger.info("Found pending job from server.")
                    process_job(server_url, token, pending_jobs)
            
        except requests.exceptions.RequestException as req_err:
            logger.warning("Network issue connecting to server, retrying in %d seconds: %s", poll_interval, req_err)
        except Exception as loop_err:
            logger.exception("Unexpected error in agent daemon loop: %s", loop_err)
            
        time.sleep(poll_interval)


if __name__ == "__main__":
    main()
