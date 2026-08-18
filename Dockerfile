FROM python:3.11-slim

# Install only ffmpeg (needed for video rendering)
RUN apt-get update && apt-get install -y \
    ffmpeg \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy requirements first (for Docker cache)
COPY requirements.txt .

# Install only lightweight dependencies
RUN pip install --no-cache-dir \
    fastapi \
    uvicorn \
    yt-dlp \
    groq \
    boto3 \
    google-api-python-client \
    google-auth-oauthlib \
    requests \
    python-multipart \
    httpx \
    pydantic

# Copy app code
COPY app/ ./app/
COPY web/ ./web/

# Create data directories
RUN mkdir -p downloads clips outputs

EXPOSE 8000

CMD uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}
