import os
import json
import logging
from datetime import datetime, timezone
from typing import Optional, List

# Google API client and auth library imports
from google_auth_oauthlib.flow import Flow
from google.oauth2.credentials import Credentials as OAuth2Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

from app.db import (
    get_setting,
    set_setting as save_setting,
    get_generated_short,
    update_short_publish_status
)

logger = logging.getLogger(__name__)

# Scopes required for YouTube upload
SCOPES = ['https://www.googleapis.com/auth/youtube.upload']

def get_oauth_client_config(redirect_uri: Optional[str] = None) -> dict:
    """
    Builds the Google OAuth client config dictionary dynamically.
    Fetches the credentials from the database settings, falling back to environment variables.
    """
    client_id = get_setting('youtube_client_id') or os.environ.get('YOUTUBE_CLIENT_ID')
    client_secret = get_setting('youtube_client_secret') or os.environ.get('YOUTUBE_CLIENT_SECRET')

    if not client_id or not client_secret:
        raise ValueError(
            "YouTube OAuth Client ID or Client Secret is not configured. "
            "Set the values in the DB settings ('youtube_client_id', 'youtube_client_secret') "
            "or as environment variables (YOUTUBE_CLIENT_ID, YOUTUBE_CLIENT_SECRET)."
        )

    config = {
        "web": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs"
        }
    }
    if redirect_uri:
        config["web"]["redirect_uris"] = [redirect_uri]
    
    return config

def get_auth_url(redirect_uri: str) -> str:
    """
    Initializes a Flow object, sets the authorization scopes, and returns the authorization URL.
    """
    client_config = get_oauth_client_config(redirect_uri)
    flow = Flow.from_client_config(
        client_config,
        scopes=SCOPES,
        redirect_uri=redirect_uri
    )
    flow.autogenerate_code_verifier = False
    auth_url, state = flow.authorization_url(
        access_type='offline',
        include_granted_scopes='true',
        prompt='consent'
    )
    from app.db import set_setting
    set_setting("youtube_oauth_state", state)
    return auth_url

def save_oauth_callback(code: str, redirect_uri: str):
    """
    Fetches token using the authorization code, converts credentials to a dictionary,
    and saves it in the database setting 'youtube_oauth_token'.
    """
    client_config = get_oauth_client_config(redirect_uri)
    flow = Flow.from_client_config(
        client_config,
        scopes=SCOPES,
        redirect_uri=redirect_uri
    )
    flow.autogenerate_code_verifier = False
    flow.fetch_token(code=code)
    credentials = flow.credentials
    
    creds_dict = json.loads(credentials.to_json())
    save_setting('youtube_oauth_token', json.dumps(creds_dict))
    logger.info("YouTube OAuth token successfully retrieved and saved to database settings.")

def upload_scheduled_short(short_id: str):
    """
    Uploads a short video to YouTube. Supports scheduled, immediate, and resumable media uploads.
    Updates short database status accordingly to 'scheduled', 'published', or 'failed'.
    """
    logger.info(f"Starting YouTube upload flow for short_id: {short_id}")
    try:
        # 1. Fetch the short metadata from the database
        short = get_generated_short(short_id)
        if not short:
            logger.error(f"Short record not found for short_id: {short_id}")
            return

        # 2. Load OAuth credentials dictionary from DB setting
        token_str = get_setting('youtube_oauth_token')
        if not token_str:
            logger.warning(f"YouTube OAuth token not configured in DB. Reverting short {short_id} to 'ready' status.")
            update_short_publish_status(short_id, 'ready')
            return

        try:
            creds_dict = json.loads(token_str)
        except Exception as e:
            logger.warning(f"Failed to parse YouTube OAuth token from database: {e}. Reverting short to 'ready' status.")
            update_short_publish_status(short_id, 'ready')
            return

        # 3. Refresh credentials using google.auth.transport.requests.Request if expired
        creds = OAuth2Credentials.from_authorized_user_info(creds_dict)
        if creds.expired and creds.refresh_token:
            logger.info("YouTube OAuth credentials expired. Attempting refresh...")
            try:
                creds.refresh(Request())
                updated_creds_dict = json.loads(creds.to_json())
                save_setting('youtube_oauth_token', json.dumps(updated_creds_dict))
            except Exception as refresh_err:
                logger.error(f"OAuth refresh failed: {refresh_err}")
                update_short_publish_status(
                    short_id,
                    'failed',
                    error=f"Failed to refresh YouTube OAuth token: {refresh_err}"
                )
                return

        # 4. Build the YouTube v3 service client
        youtube = build('youtube', 'v3', credentials=creds)

        # 5. Build MediaFileUpload object
        file_path = short.get('file_path')
        if not file_path:
            logger.error("No file path specified for the video file.")
            update_short_publish_status(
                short_id,
                'failed',
                error="Short file_path is missing from database record."
            )
            return

        if not os.path.exists(file_path):
            logger.error(f"Video file not found at local path: {file_path}")
            update_short_publish_status(
                short_id,
                'failed',
                error=f"Video file not found on filesystem at: {file_path}"
            )
            return

        media = MediaFileUpload(
            file_path,
            mimetype='video/*',
            chunksize=1024 * 1024,  # Resumable chunks of 1MB
            resumable=True
        )

        # 6. Map video metadata
        title = short.get('title') or "ClipForge Short"
        description = short.get('description') or ""
        tags = short.get('tags')
        if isinstance(tags, str):
            tags = [t.strip() for t in tags.split(",") if t.strip()]
        elif not isinstance(tags, list):
            tags = []

        scheduled_time = short.get('scheduled_publish_time')
        is_past = False

        body = {
            "snippet": {
                "title": title[:100],  # YouTube API limit: 100 characters
                "description": description,
                "tags": tags
            },
            "status": {
                "privacyStatus": "private"  # Must be private for scheduled uploads
            }
        }

        if scheduled_time:
            try:
                if isinstance(scheduled_time, str):
                    if scheduled_time.endswith("Z"):
                        dt = datetime.fromisoformat(scheduled_time[:-1]).replace(tzinfo=timezone.utc)
                    else:
                        dt = datetime.fromisoformat(scheduled_time)
                elif isinstance(scheduled_time, datetime):
                    dt = scheduled_time
                else:
                    dt = None

                if dt:
                    now = datetime.now(timezone.utc)
                    if dt < now:
                        is_past = True
                    scheduled_str = dt.strftime('%Y-%m-%dT%H:%M:%SZ')
                else:
                    scheduled_str = str(scheduled_time)

                body["status"]["publishAt"] = scheduled_str
            except Exception as pe:
                logger.warning(f"Error parsing scheduled_publish_time: {pe}. Passing string directly.")
                body["status"]["publishAt"] = str(scheduled_time)

        # If not scheduled, or scheduled in the past, defaults privacy status to public/unlisted
        if not scheduled_time or is_past:
            body["status"]["privacyStatus"] = "public"
            # Remove publishAt if it's in the past to trigger immediate publication
            if "publishAt" in body["status"]:
                del body["status"]["publishAt"]

        # 7. Resumable media upload execution
        request = youtube.videos().insert(
            part="snippet,status",
            body=body,
            media_body=media
        )

        logger.info(f"Uploading file {file_path} of size {os.path.getsize(file_path)} bytes...")
        response = None
        while response is None:
            status, response = request.next_chunk()
            if status:
                logger.info(f"Upload progress: {int(status.progress() * 100)}%")

        video_id = response.get('id')
        if not video_id:
            raise ValueError("YouTube API did not return a valid video ID.")

        logger.info(f"Upload complete. Video ID: {video_id}")

        # 8. Save the video ID and update DB record status
        target_status = 'published' if (not scheduled_time or is_past) else 'scheduled'
        update_short_publish_status(short_id, target_status, youtube_id=video_id)
        logger.info(f"Successfully marked short {short_id} as '{target_status}'.")

    except Exception as e:
        logger.exception(f"Exception raised during scheduled short upload: {e}")
        try:
            update_short_publish_status(short_id, 'failed', error=str(e))
        except Exception as db_err:
            logger.error(f"Failed to record execution error to database for short {short_id}: {db_err}")
