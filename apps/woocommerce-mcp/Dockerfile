FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
RUN pip install --no-cache-dir "mcp==1.30.0" "httpx==0.28.1" "uvicorn==0.54.0"
COPY apps/woocommerce-mcp/server.py /app/server.py

USER 10001:10001
EXPOSE 8001
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8001"]
