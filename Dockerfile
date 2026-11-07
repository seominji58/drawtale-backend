FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY alembic.ini .
COPY alembic ./alembic
COPY app ./app
# 관리 스크립트 (python -m scripts.dev_login 등). 전에는 이미지에 없어 docker cp 로 넣어야 했다 (11/7)
# dev_login 은 APP_ENV 가 local 이 아니면 아무것도 하지 않는다
COPY scripts ./scripts

RUN useradd --create-home appuser && mkdir -p /app/storage && chown appuser /app/storage
USER appuser

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
