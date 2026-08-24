import logging
import requests
import os
from typing import Optional

logger = logging.getLogger("ai_video_clipper.github_dispatch")

def dispatch_workflow(job_id: str, callback_token: str, server_url: str) -> tuple[bool, str]:
    """Dispatches a GitHub Actions workflow to run the hybrid rendering task.
    
    Reads settings from the database or environment variables.
    """
    from .db import get_setting

    # Check DB first, fallback to env var
    github_token = get_setting('github_token')
    if not github_token:
        github_token = os.environ.get('CLIPFORGE_GITHUB_TOKEN') or os.environ.get('github_token')
        
    github_repo = get_setting('github_repo')
    github_ref = get_setting('github_ref', 'main')
    github_workflow = get_setting('github_workflow', 'render.yml')

    if not github_token:
        err = "GitHub token setting is not configured (missing in DB and env vars)."
        logger.error(f"GitHub dispatch failed: {err}")
        return False, err
    if not github_repo or '/' not in github_repo:
        err = f"GitHub repository setting is invalid or not configured: {github_repo}"
        logger.error(f"GitHub dispatch failed: {err}")
        return False, err

    url = f"https://api.github.com/repos/{github_repo}/actions/workflows/{github_workflow}/dispatches"
    
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {github_token}",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "ClipForge-OpenClip-App"
    }
    
    payload = {
        "ref": github_ref,
        "inputs": {
            "job_id": job_id,
            "callback_token": callback_token,
            "server_url": server_url
        }
    }

    try:
        response = requests.post(url, headers=headers, json=payload, timeout=15)
        if response.status_code == 204:
            logger.info(f"Successfully dispatched GHA workflow '{github_workflow}' for job {job_id}")
            return True, ""
        else:
            err = f"Status {response.status_code}: {response.text or response.reason}"
            logger.error(
                f"GitHub dispatch failed for job {job_id}. "
                f"Status: {response.status_code}, Response: {response.text}"
            )
            return False, err
    except Exception as e:
        err = str(e)
        logger.exception(f"Unexpected error when dispatching GHA workflow for job {job_id}: {err}")
        return False, err
