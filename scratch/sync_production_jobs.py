import sys
import time
from pathlib import Path
import requests

# Ensure parent directory is in sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import db

PRODUCTION_URL = "https://yourautohelper-production.up.railway.app/api/jobs"

def get_production_jobs():
    try:
        response = requests.get(PRODUCTION_URL, timeout=15)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        print(f"Error fetching jobs from production: {e}")
        return []

def report_to_production(job_id, callback_token, status, s3_url):
    url = f"https://yourautohelper-production.up.railway.app/api/jobs/{job_id}/callback?token={callback_token}"
    payload = {
        "status": status,
        "progress": 100,
        "message": "Source video downloaded and uploaded to S3.",
        "s3_url": s3_url
    }
    headers = {
        "Authorization": f"Bearer {callback_token}",
        "Content-Type": "application/json"
    }
    try:
        # Try PATCH
        resp = requests.patch(url, headers=headers, json=payload, timeout=15)
        resp.raise_for_status()
        print(f"Successfully updated job {job_id} on production via PATCH.")
        return True
    except Exception as patch_err:
        print(f"PATCH callback failed for {job_id}, trying POST: {patch_err}")
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=15)
            resp.raise_for_status()
            print(f"Successfully updated job {job_id} on production via POST.")
            return True
        except Exception as post_err:
            print(f"Callback POST also failed for {job_id}: {post_err}")
            return False

def main():
    print(f"Starting Bidirectional Gateway between Railway and localhost:8000...")
    while True:
        try:
            # 1. Fetch jobs from production Railway server
            prod_jobs = get_production_jobs()
            
            # Map of production job status
            prod_jobs_map = {}
            for j in prod_jobs:
                job_id = j.get("id") or j.get("job_id")
                prod_jobs_map[job_id] = j
                
                # Check if pending job doesn't exist locally
                if j.get("status") in ["PENDING", "DOWNLOAD_QUEUED"]:
                    existing = db.get_job(job_id)
                    if not existing:
                        print(f"[NEW JOB] Syncing pending job {job_id} to local DB...")
                        db.add_job(
                            job_id=job_id,
                            source_video_id=j.get("source_video_id"),
                            status="PENDING", # Force local status to PENDING
                            request_json=j.get("request_json"),
                            callback_token=j.get("callback_token"),
                            edit_plan_json=j.get("edit_plan_json")
                        )
            
            # 2. Check local database for completed jobs and sync back to production
            # List local jobs (limit to last 50)
            local_jobs = db.list_jobs(limit=50)
            for lj in local_jobs:
                job_id = lj.get("id")
                local_status = lj.get("status")
                s3_url = lj.get("s3_url")
                
                # If local status is DOWNLOADED (or COMPLETED) and S3 URL is present
                if local_status in ["DOWNLOADED", "COMPLETED"] and s3_url:
                    # Check if the production job is still pending
                    pj = prod_jobs_map.get(job_id)
                    if pj and pj.get("status") in ["PENDING", "DOWNLOAD_QUEUED"]:
                        print(f"[SYNC BACK] Syncing completed status for job {job_id} back to Railway...")
                        success = report_to_production(
                            job_id=job_id,
                            callback_token=lj.get("callback_token"),
                            status="DOWNLOADED",
                            s3_url=s3_url
                        )
                        if success:
                            # Update local status to COMPLETED to avoid duplicate syncs
                            conn = db.get_db()
                            cursor = conn.cursor()
                            cursor.execute("UPDATE jobs SET status = 'COMPLETED' WHERE id = ?", (job_id,))
                            conn.commit()
                            print(f"Updated local job {job_id} status to COMPLETED.")

        except Exception as err:
            print(f"Error in sync loop: {err}")
            
        time.sleep(10)

if __name__ == "__main__":
    main()
