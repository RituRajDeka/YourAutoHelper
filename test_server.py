from fastapi import FastAPI, HTTPException, Request, Header, Query
from pydantic import BaseModel
from typing import Optional, List
from fastapi.testclient import TestClient

app = FastAPI()

class JobCallbackRequest(BaseModel):
    status: Optional[str] = None
    progress: Optional[float] = None
    message: Optional[str] = None
    error: Optional[str] = None
    s3_url: Optional[str] = None
    gha_run_id: Optional[str] = None
    clips: Optional[List[dict]] = None
    seo_metadata: Optional[dict] = None

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

client = TestClient(app)
payload = {
    'status': 'downloading',
    'progress': 10.0,
    'message': 'Starting download...',
    'error': None,
    's3_url': None,
    'clips': None,
    'seo_metadata': None,
    'gha_run_id': '123'
}
resp_patch = client.patch('/api/jobs/123/callback?token=abc', json=payload)
print('PATCH:', resp_patch.status_code, resp_patch.text)
resp_post = client.post('/api/jobs/123/callback?token=abc', json=payload)
print('POST:', resp_post.status_code, resp_post.text)
