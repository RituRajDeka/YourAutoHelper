import sqlite3
import json
import os
from pathlib import Path
from datetime import datetime
from typing import List, Optional, Dict, Any
from .paths import ROOT_DIR

DB_PATH = ROOT_DIR / 'shorts_factory.db'

def get_db():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    cursor = conn.cursor()
    
    # Global settings (e.g. number of shorts per day, prompts, publishing times, oauth tokens)
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY,
        value TEXT
    )
    ''')
    
    # Source YouTube channels to monitor
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS source_channels (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        url TEXT UNIQUE,
        name TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    ''')
    
    # Source videos tracked
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS source_videos (
        id TEXT PRIMARY KEY,
        url TEXT UNIQUE,
        channel_url TEXT,
        title TEXT,
        download_path TEXT,
        status TEXT, -- new, downloaded, processed, skipped
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    ''')
    
    # Generated Shorts (rendered clips)
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS generated_shorts (
        id TEXT PRIMARY KEY,
        source_video_id TEXT,
        clip_index INTEGER,
        edit_plan_json TEXT,
        file_path TEXT,
        status TEXT, -- pending, rendering, ready, uploading, scheduled, published, failed
        youtube_id TEXT,
        title TEXT,
        description TEXT,
        tags TEXT,
        scheduled_publish_time TIMESTAMP,
        publish_error TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (source_video_id) REFERENCES source_videos(id)
    )
    ''')
    
    # Job queue state (matches target scheduler states)
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS jobs (
        id TEXT PRIMARY KEY,
        source_video_id TEXT,
        status TEXT, -- PENDING, DOWNLOADING, ANALYZING, EDITING, RENDERING, SUBTITLING, QUALITY_CHECK, READY, UPLOADING, SCHEDULED, PUBLISHED, FAILED
        error TEXT,
        edit_plan_json TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (source_video_id) REFERENCES source_videos(id)
    )
    ''')
    
    # Alter table jobs to add columns if they don't exist
    columns_to_add = [
        ("progress", "INTEGER DEFAULT 0"),
        ("gha_run_id", "TEXT"),
        ("s3_url", "TEXT"),
        ("seo_title", "TEXT"),
        ("seo_description", "TEXT"),
        ("seo_tags", "TEXT"),
        ("callback_token", "TEXT"),
        ("youtube_upload_status", "TEXT DEFAULT 'ready'"),
        ("youtube_video_id", "TEXT"),
        ("request_json", "TEXT")
    ]
    for col_name, col_type in columns_to_add:
        try:
            cursor.execute(f"ALTER TABLE jobs ADD COLUMN {col_name} {col_type}")
        except sqlite3.OperationalError as e:
            if "duplicate column name" not in str(e).lower():
                raise e
    
    # Clean up corrupt channel/playlist IDs that got saved as videos in older versions
    cursor.execute("DELETE FROM source_videos WHERE length(id) != 11")
    # Reset failed source videos to new so the scheduler can automatically retry them
    cursor.execute("UPDATE source_videos SET status = 'new' WHERE status = 'failed'")
    conn.commit()
    conn.close()



# ----------------- Settings Helpers -----------------

def get_setting(key: str, default: Any = None) -> Any:
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT value FROM settings WHERE key = ?", (key,))
    row = cursor.fetchone()
    conn.close()
    if row:
        try:
            return json.loads(row['value'])
        except Exception:
            return row['value']
    return default

def set_setting(key: str, value: Any) -> None:
    conn = get_db()
    cursor = conn.cursor()
    val_str = json.dumps(value)
    cursor.execute(
        "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
        (key, val_str)
    )
    conn.commit()
    conn.close()

# ----------------- Channel Helpers -----------------

def add_source_channel(url: str, name: Optional[str] = None) -> bool:
    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "INSERT OR IGNORE INTO source_channels (url, name) VALUES (?, ?)",
            (url.strip(), name)
        )
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()

def get_source_channels() -> List[Dict[str, Any]]:
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM source_channels ORDER BY created_at DESC")
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]

def remove_source_channel(channel_id: int) -> None:
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM source_channels WHERE id = ?", (channel_id,))
    conn.commit()
    conn.close()

# ----------------- Source Video Helpers -----------------

def add_source_video(video_id: str, url: str, channel_url: str, title: str, status: str = 'new') -> bool:
    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """INSERT OR IGNORE INTO source_videos (id, url, channel_url, title, status)
               VALUES (?, ?, ?, ?, ?)""",
            (video_id, url, channel_url, title, status)
        )
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()

def update_source_video_status(video_id: str, status: str, download_path: Optional[str] = None) -> None:
    conn = get_db()
    cursor = conn.cursor()
    if download_path:
        cursor.execute(
            "UPDATE source_videos SET status = ?, download_path = ? WHERE id = ?",
            (status, download_path, video_id)
        )
    else:
        cursor.execute(
            "UPDATE source_videos SET status = ? WHERE id = ?",
            (status, video_id)
        )
    conn.commit()
    conn.close()

def get_source_video(video_id: str) -> Optional[Dict[str, Any]]:
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM source_videos WHERE id = ?", (video_id,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None

def get_videos_by_status(status: str) -> List[Dict[str, Any]]:
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM source_videos WHERE status = ? ORDER BY created_at ASC", (status,))
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]

# ----------------- Job Helpers -----------------

def add_job(
    job_id: str,
    source_video_id: str,
    status: str = 'PENDING',
    request_json: Optional[str] = None,
    callback_token: Optional[str] = None,
    edit_plan_json: Optional[str] = None
) -> None:
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        """INSERT OR REPLACE INTO jobs (id, source_video_id, status, request_json, callback_token, edit_plan_json)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (job_id, source_video_id, status, request_json, callback_token, edit_plan_json)
    )
    conn.commit()
    conn.close()

def update_job_status(job_id: str, status: str, error: Optional[str] = None, edit_plan_json: Optional[str] = None) -> None:
    conn = get_db()
    cursor = conn.cursor()
    now = datetime.now().isoformat()
    if error and edit_plan_json:
        cursor.execute(
            "UPDATE jobs SET status = ?, error = ?, edit_plan_json = ?, updated_at = ? WHERE id = ?",
            (status, error, edit_plan_json, now, job_id)
        )
    elif error:
        cursor.execute(
            "UPDATE jobs SET status = ?, error = ?, updated_at = ? WHERE id = ?",
            (status, error, now, job_id)
        )
    elif edit_plan_json:
        cursor.execute(
            "UPDATE jobs SET status = ?, edit_plan_json = ?, updated_at = ? WHERE id = ?",
            (status, edit_plan_json, now, job_id)
        )
    else:
        cursor.execute(
            "UPDATE jobs SET status = ?, updated_at = ? WHERE id = ?",
            (status, now, job_id)
        )
    conn.commit()
    conn.close()

def get_job(job_id: str) -> Optional[Dict[str, Any]]:
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        """SELECT j.*, v.url as video_url, v.title as video_title 
           FROM jobs j
           LEFT JOIN source_videos v ON j.source_video_id = v.id
           WHERE j.id = ?""",
        (job_id,)
    )
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None

def get_next_pending_job() -> Optional[Dict[str, Any]]:
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        """SELECT * FROM jobs 
           WHERE status = 'PENDING' 
           ORDER BY created_at ASC LIMIT 1"""
    )
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None

def list_jobs(limit: int = 50) -> List[Dict[str, Any]]:
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        """SELECT j.*, v.title as video_title 
           FROM jobs j
           LEFT JOIN source_videos v ON j.source_video_id = v.id
           ORDER BY j.created_at DESC LIMIT ?""",
        (limit,)
    )
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]

# ----------------- Generated Shorts Helpers -----------------

def add_generated_short(
    short_id: str,
    source_video_id: str,
    clip_index: int,
    edit_plan_json: str,
    file_path: str,
    status: str = 'pending',
    title: str = '',
    description: str = '',
    tags: str = '',
    scheduled_publish_time: Optional[str] = None
) -> None:
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        """INSERT OR REPLACE INTO generated_shorts 
           (id, source_video_id, clip_index, edit_plan_json, file_path, status, title, description, tags, scheduled_publish_time) 
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (short_id, source_video_id, clip_index, edit_plan_json, file_path, status, title, description, tags, scheduled_publish_time)
    )
    conn.commit()
    conn.close()

def update_short_publish_status(
    short_id: str,
    status: str,
    youtube_id: Optional[str] = None,
    error: Optional[str] = None
) -> None:
    conn = get_db()
    cursor = conn.cursor()
    if youtube_id and error:
        cursor.execute(
            "UPDATE generated_shorts SET status = ?, youtube_id = ?, publish_error = ? WHERE id = ?",
            (status, youtube_id, error, short_id)
        )
    elif youtube_id:
        cursor.execute(
            "UPDATE generated_shorts SET status = ?, youtube_id = ?, publish_error = NULL WHERE id = ?",
            (status, youtube_id, short_id)
        )
    elif error:
        cursor.execute(
            "UPDATE generated_shorts SET status = ?, publish_error = ? WHERE id = ?",
            (status, error, short_id)
        )
    else:
        cursor.execute(
            "UPDATE generated_shorts SET status = ? WHERE id = ?",
            (status, short_id)
        )
    conn.commit()
    conn.close()

def get_generated_short(short_id: str) -> Optional[Dict[str, Any]]:
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM generated_shorts WHERE id = ?", (short_id,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None

def get_shorts_by_status(status: str) -> List[Dict[str, Any]]:
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM generated_shorts WHERE status = ? ORDER BY created_at ASC", (status,))
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]

def update_job_progress(
    job_id: str,
    status: str,
    progress: int,
    error: Optional[str] = None,
    s3_url: Optional[str] = None,
    gha_run_id: Optional[str] = None
) -> None:
    conn = get_db()
    cursor = conn.cursor()
    now = datetime.now().isoformat()
    cursor.execute(
        """UPDATE jobs 
           SET status = ?, progress = ?, error = ?, s3_url = ?, gha_run_id = ?, updated_at = ? 
           WHERE id = ?""",
        (status, progress, error, s3_url, gha_run_id, now, job_id)
    )
    conn.commit()
    conn.close()

def update_job_seo(job_id: str, title: str, description: str, tags: str) -> None:
    conn = get_db()
    cursor = conn.cursor()
    now = datetime.now().isoformat()
    cursor.execute(
        """UPDATE jobs 
           SET seo_title = ?, seo_description = ?, seo_tags = ?, updated_at = ? 
           WHERE id = ?""",
        (title, description, tags, now, job_id)
    )
    conn.commit()
    conn.close()

def update_job_youtube(job_id: str, upload_status: str, video_id: Optional[str] = None) -> None:
    conn = get_db()
    cursor = conn.cursor()
    now = datetime.now().isoformat()
    cursor.execute(
        """UPDATE jobs 
           SET youtube_upload_status = ?, youtube_video_id = ?, updated_at = ? 
           WHERE id = ?""",
        (upload_status, video_id, now, job_id)
    )
    conn.commit()
    conn.close()
def get_shorts_by_source_video_id(source_video_id: str) -> List[Dict[str, Any]]:
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT * FROM generated_shorts WHERE source_video_id = ? ORDER BY clip_index ASC",
        (source_video_id,)
    )
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]

def update_job_from_callback(
    job_id: str,
    status: Optional[str] = None,
    progress: Optional[int] = None,
    error: Optional[str] = None,
    s3_url: Optional[str] = None,
    gha_run_id: Optional[str] = None,
    seo_metadata: Optional[Dict[str, Any]] = None,
    clips: Optional[List[Dict[str, Any]]] = None
) -> None:
    conn = get_db()
    cursor = conn.cursor()
    now = datetime.now().isoformat()
    
    cursor.execute("SELECT source_video_id, edit_plan_json FROM jobs WHERE id = ?", (job_id,))
    job_row = cursor.fetchone()
    source_video_id = job_row['source_video_id'] if job_row else None
    edit_plan_json = job_row['edit_plan_json'] if job_row else None
    
    updates = []
    params = []
    if status is not None:
        updates.append("status = ?")
        params.append(status)
    if progress is not None:
        updates.append("progress = ?")
        params.append(progress)
    if error is not None:
        updates.append("error = ?")
        params.append(error)
    if s3_url is not None:
        updates.append("s3_url = ?")
        params.append(s3_url)
    if gha_run_id is not None:
        updates.append("gha_run_id = ?")
        params.append(gha_run_id)
        
    if seo_metadata:
        if 'title' in seo_metadata:
            updates.append("seo_title = ?")
            params.append(seo_metadata['title'])
        if 'description' in seo_metadata:
            updates.append("seo_description = ?")
            params.append(seo_metadata['description'])
        if 'tags' in seo_metadata:
            tags_val = seo_metadata['tags']
            if isinstance(tags_val, list):
                tags_val = ",".join(tags_val)
            updates.append("seo_tags = ?")
            params.append(tags_val)
            
    if updates:
        updates.append("updated_at = ?")
        params.append(now)
        params.append(job_id)
        query = f"UPDATE jobs SET {', '.join(updates)} WHERE id = ?"
        cursor.execute(query, tuple(params))
        
    conn.commit()
    conn.close()
    
    if clips and source_video_id:
        for clip in clips:
            clip_idx = clip.get('index') if clip.get('index') is not None else clip.get('clip_index', 0)
            short_id = clip.get('id') or clip.get('clip_id') or clip.get('short_id') or f"shorts_{job_id}_{clip_idx}"
            c_title = clip.get('title') or ""
            c_desc = clip.get('description') or (seo_metadata.get('description') if seo_metadata else "")
            c_tags = clip.get('tags') or (seo_metadata.get('tags') if seo_metadata else "")
            if isinstance(c_tags, list):
                c_tags = ",".join(c_tags)
            c_status = clip.get('status') or 'ready'
            c_file_path = clip.get('file_path') or clip.get('url') or clip.get('s3_url') or ""
            c_pub_time = clip.get('scheduled_publish_time')
            c_edit_plan = clip.get('edit_plan_json') or edit_plan_json
            
            # Since edit_plan_json might be a dict or string, let's normalize it to string
            if isinstance(c_edit_plan, dict):
                c_edit_plan = json.dumps(c_edit_plan)
                
            add_generated_short(
                short_id=short_id,
                source_video_id=source_video_id,
                clip_index=clip_idx,
                edit_plan_json=c_edit_plan,
                file_path=c_file_path,
                status=c_status,
                title=c_title,
                description=c_desc,
                tags=c_tags,
                scheduled_publish_time=c_pub_time
            )

def get_last_job_info() -> Optional[Dict[str, Any]]:
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM jobs ORDER BY updated_at DESC LIMIT 1")
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None

def get_last_published_short() -> Optional[Dict[str, Any]]:
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM generated_shorts WHERE status = 'published' ORDER BY created_at DESC LIMIT 1")
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None

def cancel_all_active_jobs() -> None:
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("UPDATE jobs SET status = 'CANCELLED' WHERE status IN ('PENDING', 'RUNNING', 'DOWNLOADING', 'ANALYZING', 'EDITING', 'RENDERING', 'SUBTITLING', 'QUALITY_CHECK', 'UPLOADING')")
    conn.commit()
    conn.close()

# Initialize tables
init_db()

