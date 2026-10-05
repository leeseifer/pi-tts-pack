FROM python:3.11-slim-bookworm

ARG FULL=0
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/opt/pi-tts-pack \
    PIPER_HOME=/data \
    PIPER_HOST=0.0.0.0 \
    PIPER_PORT=5050 \
    PIPER_HTTPS_PORT=0 \
    PIPER_MAX_LOADED_VOICES=4 \
    TTS_CONCURRENCY=1 \
    HF_HOME=/data/huggingface \
    XDG_CACHE_HOME=/data/cache \
    PI_TTS_PACK_FULL=${FULL}

WORKDIR /opt/pi-tts-pack
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg libsndfile1 espeak-ng libespeak-ng1 libgomp1 openssl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY requirements*.txt ./
RUN python -m pip install --no-cache-dir --upgrade pip setuptools wheel \
    && if [ "$FULL" = 1 ]; then \
         python -m pip install --no-cache-dir 'torch==2.14.0+cpu' --index-url https://download.pytorch.org/whl/cpu \
         && python -m pip install --no-cache-dir -r requirements-pi5.txt --extra-index-url https://download.pytorch.org/whl/cpu; \
       else python -m pip install --no-cache-dir -r requirements-core.txt; fi \
    && python -m pip check

COPY app/ app/
COPY scripts/ scripts/
COPY docker/ docker/
COPY LICENSE ./
RUN groupadd --gid 10001 tts && useradd --uid 10001 --gid tts --create-home tts \
    && mkdir /data && chown tts:tts /data \
    && chmod 755 docker/entrypoint.sh
USER tts
EXPOSE 5050
HEALTHCHECK --interval=10s --timeout=5s --start-period=15m --retries=3 \
    CMD python -c "import json,urllib.request; d=json.load(urllib.request.urlopen('http://127.0.0.1:5050/health',timeout=4)); assert d['ok'] and d['voices'] >= 2"
ENTRYPOINT ["/opt/pi-tts-pack/docker/entrypoint.sh"]
CMD ["python", "-m", "app.main"]
