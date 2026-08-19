FROM python:3.11-slim-bullseye
WORKDIR /app

# Install ffmpeg, curl, and system dependencies
RUN apt-get update && apt-get install -y \
    ffmpeg \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy python dependencies and install lightweight list
COPY requirements-deploy.txt .
RUN pip install --no-cache-dir -r requirements-deploy.txt

# Copy backend app source code and pre-compiled frontend assets
COPY app/ ./app/
COPY web/ ./web/

# Create data directories
RUN mkdir -p downloads clips outputs

EXPOSE 8000

CMD uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}
