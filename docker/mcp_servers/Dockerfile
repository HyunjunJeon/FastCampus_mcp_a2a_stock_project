# MCP Servers를 위한 공통 Dockerfile
FROM python:3.12-slim

# 작업 디렉토리 설정
WORKDIR /app

# 시스템 패키지 설치 (healthcheck용 curl 포함)
RUN apt-get update && apt-get install -y \
    curl \
    && rm -rf /var/lib/apt/lists/*

# uv 설치 (공식 multi-stage copy 방식)
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Python 의존성 파일 복사
# Note: build context가 프로젝트 루트(../../)이므로 상대 경로 사용
COPY pyproject.toml uv.lock ./

# uv sync로 의존성 설치 (lock 파일 기반 재현 가능한 설치)
# --frozen: lock 파일을 그대로 사용 (업데이트 시도 안함)
# --no-dev: 개발 의존성 제외
RUN uv sync --frozen --no-dev

# 소스코드 복사
COPY src ./src

# 환경 변수 설정
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app
ENV MCP_PORT=9000
ENV PATH="/app/.venv/bin:$PATH"

# 실행 사용자 생성 (보안)
RUN adduser --disabled-password --gecos '' appuser && \
    chown -R appuser:appuser /app

USER appuser

# 기본 포트 노출
EXPOSE 9000
