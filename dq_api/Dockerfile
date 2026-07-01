FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py .

EXPOSE 8000

# 컨테이너 실행 시 ICEBERG_WAREHOUSE_PATH / AWS 자격증명(env 또는 IAM Role) 필요
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
