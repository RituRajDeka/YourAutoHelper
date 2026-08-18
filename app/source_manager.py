import logging
import yt_dlp
from pathlib import Path
from typing import Optional
from .db import get_source_channels, add_source_video, update_source_video_status, get_source_video
from .downloader import download_video
from .paths import DOWNLOADS_DIR

logger = logging.getLogger("ai_video_clipper.source_manager")

def sync_channels(max_results: int = 5) -> int:
    """
    Polls all source channels in the database and adds new videos to the source_videos table.
    Returns the count of newly discovered videos.
    """
    channels = get_source_channels()
    new_count = 0
    
    ydl_opts = {
        'extract_flat': 'in_playlist',
        'playlistend': max_results,
        'quiet': True,
        'no_warnings': True,
    }
    
    for ch in channels:
        url = ch['url']
        name = ch['name'] or url
        logger.info("Polling channel %s...", name)
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            try:
                info = ydl.extract_info(url, download=False)
                if not info or 'entries' not in info:
                    logger.warning("No entries found for channel %s", url)
                    continue
                    
                for entry in info['entries']:
                    if not entry:
                        continue
                    video_id = entry.get('id')
                    video_url = entry.get('url') or f"https://www.youtube.com/watch?v={video_id}"
                    title = entry.get('title') or f"Video {video_id}"
                    
                    if not video_id:
                        continue
                        
                    # Add to database if not exists
                    added = add_source_video(
                        video_id=video_id,
                        url=video_url,
                        channel_url=url,
                        title=title,
                        status='new'
                    )
                    if added:
                        new_count += 1
                        logger.info("Discovered new video: %s (ID: %s)", title, video_id)
            except Exception as e:
                logger.error("Failed to poll channel %s: %s", url, e)
                
    return new_count

def download_video_source(video_id: str) -> Optional[Path]:
    """
    Downloads the source video for the given video_id and updates database status.
    """
    video = get_source_video(video_id)
    if not video:
        logger.error("Video ID %s not found in database", video_id)
        return None
        
    url = video['url']
    logger.info("Downloading source video %s (ID: %s)...", video['title'], video_id)
    update_source_video_status(video_id, 'downloading')
    
    try:
        download_path = download_video(url)
        # Rename or use the downloaded path directly
        update_source_video_status(video_id, 'downloaded', str(download_path.resolve()))
        logger.info("Successfully downloaded video %s to %s", video_id, download_path)
        return download_path
    except Exception as e:
        logger.error("Failed to download video %s: %s", video_id, e)
        update_source_video_status(video_id, 'failed')
        return None
