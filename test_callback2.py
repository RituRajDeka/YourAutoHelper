import requests
import os
import json
url = 'https://yourautohelper-production.up.railway.app/api/jobs/cfd339c47e5c402fbdb8940d786521d3/callback?token=ce896e9ebb42d241aded5e0c84dfb307'
payload = {
    'status': 'downloading',
    'progress': 10.00,
    'message': 'Starting download...',
    'error': None,
    's3_url': None,
    'clips': None,
    'seo_metadata': None,
    'gha_run_id': '12345'
}
headers = {'Content-Type': 'application/json', 'Authorization': 'Bearer ce896e9ebb42d241aded5e0c84dfb307'}
print('PATCH:', requests.patch(url, headers=headers, json=payload).text)
print('POST:', requests.post(url, headers=headers, json=payload).text)
