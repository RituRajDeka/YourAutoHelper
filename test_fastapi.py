from fastapi import FastAPI
from fastapi.testclient import TestClient

app = FastAPI()

@app.post('/api')
def post_route(): return 'ok'

client = TestClient(app)
print(client.patch('/api').status_code)
