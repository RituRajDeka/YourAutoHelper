# Multi-stage build: builder stage for Node assets
FROM node:20-alpine AS frontend-builder
WORKDIR /build
COPY package*.json tsconfig.json vite.config.ts ./
COPY web/ ./web/
RUN npm ci && npm run build

# Python runner stage
FROM python:3.11-slim
WORKDIR /app

# Install ffmpeg, curl, and system dependencies
RUN apt-get update && apt-get install -y \
    ffmpeg \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy python dependencies and install lightweight list
COPY requirements-deploy.txt .
RUN pip install --no-cache-dir -r requirements-deploy.txt

# Copy built frontend assets from builder stage
COPY --from=frontend-builder /build/web/dist ./web/dist

# Copy backend app source code
COPY app/ ./app/

# Create data directories
RUN mkdir -p downloads clips outputs

EXPOSE 8000

CMD uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}
