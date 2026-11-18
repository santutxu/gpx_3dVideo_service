FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg libegl1 libgl1-mesa-dri libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .

ENV GPX_OUTPUT_DIR=/data/output \
    GPX_TILE_CACHE_DIR=/data/tile_cache \
    GPX_DB_PATH=/data/gpx_render.db \
    GPX_FFMPEG_BINARY=ffmpeg

VOLUME ["/data"]
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
