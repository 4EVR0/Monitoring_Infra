FROM python:3.12-slim

# 평가 코드(4EVR0-Server eval/requirements.txt)와 같은 MLflow 버전. boto3는 S3 artifact 저장용.
RUN pip install --no-cache-dir mlflow==3.14.0 boto3==1.43.36

EXPOSE 5000
# 실행 옵션은 docker-compose.yml(command)에서 지정한다.
ENTRYPOINT ["mlflow", "server"]
