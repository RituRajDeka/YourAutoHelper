# ─────────────────────────────────────────────────────────────────────────────
# Stage 1 — build dependencies
# ─────────────────────────────────────────────────────────────────────────────
FROM python:3.11-slim AS builder

WORKDIR /build

# System deps needed for compilation (e.g. numpy wheels, Pillow)
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        git \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --upgrade pip \
 && pip install --no-cache-dir --prefix=/install -r requirements.txt


# ─────────────────────────────────────────────────────────────────────────────
# Stage 2 — runtime image
# ─────────────────────────────────────────────────────────────────────────────
FROM python:3.11-slim AS runtime

# Runtime system deps
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg \
        curl \
        git \
    && rm -rf /var/lib/apt/lists/*

# Create a non-root service user
RUN useradd -m -s /bin/bash clipforge

WORKDIR /app

# Copy installed packages from builder
COPY --from=builder /install /usr/local

# Copy application source
COPY . .

# Data directories (mounted as volumes in production)
RUN mkdir -p clips downloads transcripts uploads assets/music assets/sounds \
 && chown -R clipforge:clipforge /app

USER clipforge

EXPOSE 8000

# Health check — hits the /health endpoint
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Run with Uvicorn; number of workers is configurable via env
CMD uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}
