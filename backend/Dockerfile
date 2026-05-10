FROM python:3.11-slim

# System deps: ffmpeg for audio processing, mp4chaps for chapter embedding
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# mp4chaps — install from source if not in apt
RUN apt-get update && apt-get install -y --no-install-recommends \
    mp4v2-utils 2>/dev/null || true && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps before copying source (better layer caching)
COPY pyproject.toml .
RUN pip install --no-cache-dir -e ".[dev]" 2>/dev/null || pip install --no-cache-dir -e .

# Copy application source
COPY app/ ./app/

# Create storage and data dirs (will be overridden by volume mounts)
RUN mkdir -p /app/storage/pdfs /app/storage/audio /app/storage/m4b \
             /app/storage/reference /app/data

# Non-root user for safety
RUN useradd -r -u 1001 appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
