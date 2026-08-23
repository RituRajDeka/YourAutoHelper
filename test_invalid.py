import requests
url = 'https://yourautohelper-production.up.railway.app/api/jobs/made_up_job_id_123/callback?token=invalid_token'
payload = {'status': 'downloading', 'progress': 10.0}
print('PATCH:', requests.patch(url, json=payload))
print('POST:', requests.post(url, json=payload))
