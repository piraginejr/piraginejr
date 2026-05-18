FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    POWER_CHURCH_ENV=server \
    POWER_CHURCH_HOST=0.0.0.0 \
    POWER_CHURCH_PORT=8000 \
    POWER_CHURCH_DB_PATH=/app/data/power_church_membros_importado.db \
    POWER_CHURCH_PDF_PROVIDER=pymupdf

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        sqlite3 \
        tesseract-ocr \
        tesseract-ocr-por \
    && rm -rf /var/lib/apt/lists/*

COPY deploy/requirements/base.txt /tmp/requirements-base.txt
RUN pip install --no-cache-dir -r /tmp/requirements-base.txt

COPY . /app
RUN mkdir -p /app/data /app/data/homologacao /app/data/pix_uploads /app/data/statement_uploads /app/data/people_uploads /app/data/envelope_uploads

EXPOSE 8000

HEALTHCHECK --interval=60s --timeout=20s --start-period=20s --retries=3 \
    CMD python scripts/verificar_dependencias_servidor.py --profile server --db "$POWER_CHURCH_DB_PATH" || exit 1

CMD ["python", "power_church_demo.py", "--no-browser"]
