import requests
url = 'http://127.0.0.1:8000/api/jobs/testjob/callback?token=testtoken'
payload = {'status': 'downloading', 'progress': 10.0, 'message': 'Starting download...', 'error': None, 's3_url': None, 'clips': None, 'seo_metadata': None, 'gha_run_id': '123'}
headers = {'Authorization': 'Bearer testtoken', 'Content-Type': 'application/json'}
p = requests.patch(url, json=payload, headers=headers)
print('PATCH:', p.status_code, p.text)
p = requests.post(url, json=payload, headers=headers)
print('POST:', p.status_code, p.text)
