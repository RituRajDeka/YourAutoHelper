import requests

token = "YOUR_GITHUB_PAT_HERE"
repo = "RituRajDeka/YourAutoHelper"
run_id = 32590145209

headers = {
    "Authorization": f"token {token}",
    "Accept": "application/vnd.github.v3+json"
}

# 1. List jobs for run
jobs_url = f"https://api.github.com/repos/{repo}/actions/runs/{run_id}/jobs"
resp = requests.get(jobs_url, headers=headers)
jobs = resp.json().get("jobs", [])
if not jobs:
    print("No jobs found.")
    exit(1)

job_id = jobs[0]["id"]
print(f"Failed Job ID: {job_id}")

# 2. Fetch logs for job
logs_url = f"https://api.github.com/repos/{repo}/actions/jobs/{job_id}/logs"
print(f"Fetching logs from {logs_url}...")
logs_resp = requests.get(logs_url, headers=headers)
if logs_resp.status_code == 200:
    print("--- LOG CONTENT START ---")
    # Print the last 150 lines of the logs to keep it readable
    lines = logs_resp.text.split("\n")
    for line in lines[-150:]:
        print(line)
    print("--- LOG CONTENT END ---")
else:
    print(f"Failed to fetch logs: {logs_resp.status_code}")
    print(logs_resp.text)
