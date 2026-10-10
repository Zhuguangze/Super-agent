# Super Agent 镜像（ticket 15）——多阶段：node 构建前端 → python 运行时托管
# 构建：docker build -t super-agent -f deploy/Dockerfile .
FROM node:22-slim AS frontend
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim

WORKDIR /app

COPY backend/pyproject.toml /app/backend/
COPY backend/src /app/backend/src
RUN pip install --no-cache-dir /app/backend

# 前端构建产物由后端进程托管（单容器即完整 UI）
COPY --from=frontend /app/frontend/dist /app/frontend_dist
ENV SUPER_AGENT_FRONTEND_DIST=/app/frontend_dist

# 数据目录（容器内挂载点：sessions / memory / workspaces / extensions / settings / mcp.json）
ENV SUPER_AGENT_DATA=/data
VOLUME ["/data"]

EXPOSE 8000

CMD ["uvicorn", "super_agent.server.main:app", "--host", "0.0.0.0", "--port", "8000"]
