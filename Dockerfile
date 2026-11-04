# 喵艺 AI 伴侣 · 后端 Dockerfile
# ================================

FROM python:3.10-slim

# 环境变量
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    ENVIRONMENT=production

# 设置工作目录
WORKDIR /app

# 安装系统依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# 复制依赖文件并安装
COPY requirements.txt requirements-prod.txt ./
RUN pip install --no-cache-dir -r requirements-prod.txt

# 复制项目代码
COPY . .

# 创建必要目录
RUN mkdir -p data backups logs

# 创建非 root 用户
RUN groupadd -r miaoyi && useradd -r -g miaoyi -d /app miaoyi \
    && chown -R miaoyi:miaoyi /app

USER miaoyi

# 健康检查
HEALTHCHECK --interval=30s --timeout=5s --retries=3 --start-period=10s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8001/api/health')"

# 暴露端口
EXPOSE 8001

# 启动命令
CMD ["python", "server.py"]
