from fastapi import FastAPI, HTTPException, Request, Header, Query
from pydantic import BaseModel
from typing import Optional, List
import uvicorn
import threading
import time
import requests

app = FastAPI()

class JobCallbackRequest(BaseModel):
    status: Optional[str] = None
    progress: Optional[float] = None
    message: Optional[str] = None

@app.post('/api/jobs/{job_id}/callback')
@app.patch('/api/jobs/{job_id}/callback')
def job_callback(
    request: Request,
    job_id: str,
    req: JobCallbackRequest,
    authorization: Optional[str] = Header(None),
    token: Optional[str] = Query(None)
) -> dict:
    raise HTTPException(status_code=404, detail='Job not found.')

def run_server():
    uvicorn.run(app, host='127.0.0.1', port=8000, log_level='error')

t = threading.Thread(target=run_server, daemon=True)
t.start()
time.sleep(1)

url = 'http://127.0.0.1:8000/api/jobs/123/callback?token=abc'
payload = {'status': 'downloading', 'progress': 10.0, 'message': 'test'}
headers = {'Authorization': 'Bearer abc'}
print('PATCH:', requests.patch(url, json=payload, headers=headers).status_code)
print('POST:', requests.post(url, json=payload, headers=headers).status_code)
