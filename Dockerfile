FROM python:3.12-slim

WORKDIR /app

# 시스템 의존성 (pykrx 등이 필요로 하는 것)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# 의존성만 먼저 복사해서 레이어 캐시 활용
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 소스 복사
COPY app/ ./app/

# 데이터·정적 파일 디렉터리
RUN mkdir -p data static

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')"

CMD ["uvicorn", "app.entrypoints.web:app", "--host", "0.0.0.0", "--port", "8000", "--log-level", "info"]
