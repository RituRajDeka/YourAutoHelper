from fastapi import FastAPI, Header, Query
from pydantic import BaseModel
from typing import Optional
from fastapi.testclient import TestClient

app = FastAPI()

class JobCallbackRequest(BaseModel):
    status: Optional[str] = None

@app.patch('/api/jobs/callback')
def job_callback(
    req: JobCallbackRequest,
    authorization: Optional[str] = Header(None),
    token: Optional[str] = Query(None)
):
    return 'ok'

client = TestClient(app)
payload = {'status': 'downloading'}
resp = client.patch('/api/jobs/callback?token=abc', json=payload, headers={'Authorization': 'Bearer abc'})
print(resp.status_code, resp.text)
