FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /srv

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

# Cloud Run injects PORT; default to 8080 for local runs.
CMD exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080}
