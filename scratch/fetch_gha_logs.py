import requests
import json

token = "YOUR_GITHUB_PAT_HERE"
repo = "RituRajDeka/YourAutoHelper"

headers = {
    "Authorization": f"token {token}",
    "Accept": "application/vnd.github.v3+json"
}

url = f"https://api.github.com/repos/{repo}/actions/runs?per_page=5"
print(f"Fetching GHA runs from {url}...")
resp = requests.get(url, headers=headers)
if resp.status_code != 200:
    print(f"Error: {resp.status_code}")
    print(resp.text)
    exit(1)

runs = resp.json().get("workflow_runs", [])
if not runs:
    print("No runs found.")
    exit(0)

for run in runs:
    print(f"\nRun ID: {run['id']}")
    print(f"Event: {run['event']}")
    print(f"Status: {run['status']}")
    print(f"Conclusion: {run['conclusion']}")
    print(f"URL: {run['html_url']}")
    print(f"Created At: {run['created_at']}")
    
    # If the run failed, let's fetch jobs
    if run['conclusion'] == 'failure':
        jobs_url = run['jobs_url']
        jobs_resp = requests.get(jobs_url, headers=headers)
        if jobs_resp.status_code == 200:
            jobs_data = jobs_resp.json().get("jobs", [])
            for job in jobs_data:
                print(f"  Job Name: {job['name']}")
                print(f"  Job Status: {job['status']}")
                print(f"  Job Conclusion: {job['conclusion']}")
                # print steps
                for step in job.get("steps", []):
                    print(f"    Step: {step['name']} ({step['conclusion']})")
