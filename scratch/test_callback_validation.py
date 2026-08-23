import requests
import json

url = "https://yourautohelper-production.up.railway.app/api/jobs/315f2434ddb94418a5efff8926d93cb9/callback?token=6cea55fbeffc0270871992fb1e790511"

payload = {
    "status": "downloading",
    "progress": 10.0,
    "message": "Starting download...",
    "error": None,
    "s3_url": None,
    "clips": None,
    "seo_metadata": None,
    "gha_run_id": "test_run_123"
}

headers = {
    "Authorization": "Bearer 6cea55fbeffc0270871992fb1e790511",
    "Content-Type": "application/json"
}

print("Sending PATCH request...")
resp = requests.patch(url, headers=headers, json=payload)
print(f"Status Code: {resp.status_code}")
try:
    print(json.dumps(resp.json(), indent=2))
except Exception as e:
    print(f"Response text (could not parse as JSON): {resp.text}")
