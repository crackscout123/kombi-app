# ---- Build-Stage: Abhängigkeiten in ein venv installieren
FROM python:3.12-slim AS builder
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
WORKDIR /build
COPY requirements.txt .
RUN pip install -r requirements.txt

# ---- Runtime-Stage: schlank, ohne Root
FROM python:3.12-slim
ENV PATH="/opt/venv/bin:$PATH" PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PORT=8000 TZ=Europe/Berlin
RUN groupadd -g 10001 app && useradd -u 10001 -g app -M -s /usr/sbin/nologin app
WORKDIR /srv/kombi
COPY --from=builder /opt/venv /opt/venv
COPY --chown=app:app app ./app
COPY --chown=app:app wsgi.py ./
USER app
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
  CMD python -c "import os,urllib.request;urllib.request.urlopen('http://127.0.0.1:%s/health' % os.environ.get('PORT','8000'), timeout=4)"
# --preload: Migrationen/Seed laufen einmal im Master (kein Worker-Timeout beim DB-Warten)
CMD ["sh", "-c", "exec gunicorn --preload -b 0.0.0.0:${PORT} -w ${WEB_CONCURRENCY:-2} --timeout 60 --access-logfile - --forwarded-allow-ips='*' wsgi:app"]
