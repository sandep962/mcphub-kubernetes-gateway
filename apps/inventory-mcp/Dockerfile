
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN pip install --no-cache-dir "mcp==1.30.0" "uvicorn==0.54.0" "PyMySQL==1.1.1"

COPY apps/inventory-mcp/server.py /app/server.py
COPY apps/shared /app/apps/shared
RUN touch /app/apps/__init__.py

USER 10001:10001

EXPOSE 8002

CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8002"]
