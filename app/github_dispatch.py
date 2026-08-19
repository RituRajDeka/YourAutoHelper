import logging
import requests
from typing import Optional

logger = logging.getLogger("ai_video_clipper.github_dispatch")

def dispatch_workflow(job_id: str, callback_token: str, server_url: str) -> tuple[bool, str]:
    """Dispatches a GitHub Actions workflow to run the hybrid rendering task.
    
    Reads settings from the database:
      - github_token: Personal access token with repo/actions scope.
      - github_repo: Repository path in format "owner/repo".
      - github_ref: Git ref (branch or tag) to trigger (defaults to "main").
      - github_workflow: The workflow filename or ID (defaults to "render.yml").
      
    Sends a POST request to:
      https://api.github.com/repos/{owner}/{repo}/actions/workflows/{workflow}/dispatches
      
    Args:
        job_id: The ID of the job being executed.
        callback_token: Token to authenticate callback requests.
        server_url: The callback URL prefix of the server.
        
    Returns:
        tuple[bool, str]: (True, "") if the dispatch was successful (status 204), (False, error_msg) otherwise.
    """
    from .db import get_setting

    github_token = get_setting('github_token')
    github_repo = get_setting('github_repo')
    github_ref = get_setting('github_ref', 'main')
    github_workflow = get_setting('github_workflow', 'render.yml')

    if not github_token:
        err = "GitHub token setting is not configured."
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

