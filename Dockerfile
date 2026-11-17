FROM python:3.14-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    GUARDCART_DATABASE_PATH=/data/guardcart.sqlite3

WORKDIR /app
COPY requirements.lock ./
RUN python -m pip install --no-cache-dir -r requirements.lock \
    && groupadd --gid 10001 guardcart \
    && useradd --uid 10001 --gid guardcart --no-create-home guardcart \
    && mkdir /data && chown guardcart:guardcart /data

# Deliberately copy runtime inputs only. No dotfiles, keys, database or reports.
COPY backend/ ./backend/
COPY data/ ./data/
COPY frontend/dist/ ./frontend/dist/

USER 10001:10001
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import json,os,urllib.request; p=os.environ.get('PORT','8000'); h=os.environ.get('GUARDCART_ALLOWED_HOSTS','127.0.0.1').split(',')[0].strip(); r=urllib.request.urlopen(urllib.request.Request('http://127.0.0.1:'+p+'/api/health',headers={'Host':h}),timeout=3); assert json.load(r)['status']=='ok'"
CMD ["python", "-B", "-m", "backend.server", "--host", "0.0.0.0"]
