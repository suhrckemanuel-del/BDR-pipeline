# BDR Pipeline — Streamlit dashboard image.
# Build:  docker build -t bdr-pipeline .
# Run:    docker run -p 8501:8501 -v bdr-data:/data --env-file .env bdr-pipeline
# The app boots with zero API keys (offline sample mode); keys unlock live runs.
FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# SQLite lives on /data so a mounted volume survives container replacement.
RUN mkdir -p /data
ENV BDR_DB_PATH=/data/bdr.db \
    PYTHONUNBUFFERED=1

VOLUME /data
EXPOSE 8501

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8501/_stcore/health')" || exit 1

CMD ["streamlit", "run", "app/main.py", "--server.port=8501", "--server.address=0.0.0.0", "--server.headless=true"]
