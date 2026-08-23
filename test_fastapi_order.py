from fastapi import FastAPI, Request
from pydantic import BaseModel
from fastapi.testclient import TestClient

app = FastAPI()

class JobCallbackRequest(BaseModel):
    status: str

@app.post('/api/jobs/callback')
@app.patch('/api/jobs/callback')
def job_callback(
    req: JobCallbackRequest,
    request: Request
):
    return 'ok'

client = TestClient(app)
payload = {'status': 'downloading'}
print('PATCH:', client.patch('/api/jobs/callback', json=payload).status_code)
print('POST:', client.post('/api/jobs/callback', json=payload).status_code)
