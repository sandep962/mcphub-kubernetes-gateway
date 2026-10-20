FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1

WORKDIR /app

RUN pip install --no-cache-dir "fastapi==0.143.2" "mcp==1.30.0" "uvicorn==0.54.0" "httpx==0.28.1" "PyMySQL==1.1.1"

COPY apps/gateway/app /app/apps/gateway/app
COPY apps/shared /app/apps/shared

RUN touch /app/apps/__init__.py

USER 10001:10001

EXPOSE 8000

CMD ["uvicorn", "apps.gateway.app.main:app", "--host", "0.0.0.0", "--port", "8000"]
