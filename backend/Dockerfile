# 接口进程与任务进程共用的镜像。构建上下文为仓库根目录。
FROM python:3.12-slim

ARG PIP_INDEX_URL=https://pypi.org/simple
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/srv/backend TZ=Asia/Shanghai

WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -i ${PIP_INDEX_URL} -r requirements.txt

COPY backend/ backend/
WORKDIR /srv/backend
