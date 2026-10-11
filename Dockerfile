# Web app: FastAPI backend + UI. Works locally, on Hugging Face Spaces (Docker SDK) and on Render.
#   docker build -t airraid .
#   docker run -p 7860:7860 -e ALERTS_IN_UA_TOKEN=... airraid
FROM python:3.12-slim

# LightGBM needs OpenMP at runtime
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*

# Hugging Face Spaces runs containers as uid 1000
RUN useradd -m -u 1000 app
WORKDIR /app

COPY pyproject.toml ./
COPY docs/web-app.md docs/web-app.md
COPY src ./src
RUN pip install --no-cache-dir .

COPY --chown=app . .
RUN mkdir -p /app/var /app/data/raw /app/reports && chown -R app /app/var /app/data /app/reports
USER app

ENV PORT=7860 \
    AIRRAID_DATA_PATH=/app/data/raw/volunteer_data_en.csv \
    AIRRAID_MODEL_DIR=/app/var/models \
    AIRRAID_REPORTS_DIR=/app/reports \
    PYTHONUNBUFFERED=1
EXPOSE 7860

HEALTHCHECK --interval=60s --timeout=5s --start-period=30s CMD python -c "import urllib.request,os; urllib.request.urlopen(f'http://127.0.0.1:{os.environ[\"PORT\"]}/api/health', timeout=4)"

CMD ["sh", "-c", "airraid-app serve --host 0.0.0.0 --port ${PORT}"]
