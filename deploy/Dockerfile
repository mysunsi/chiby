# AI Ops Assistant — 生产镜像（FastAPI + Celery Worker 共用）
FROM python:3.10-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# 项目依赖（与仓库根目录 requirements.txt 一致）
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 应用代码（构建上下文应为仓库根目录：docker compose 中已指定）
COPY . .

EXPOSE 8000

# 健康检查见 deploy/docker-compose.yml 中 app 服务（worker 与 app 共用镜像，不在此声明以免 worker 误判不健康）

# 默认启动 FastAPI（worker 服务在 compose 中覆盖 command）
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
