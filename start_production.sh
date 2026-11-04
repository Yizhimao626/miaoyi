#!/bin/bash
# 喵艺 AI 生产环境启动脚本

set -e

echo "=== 喵艺 AI 服务器启动 ==="

# 检查必要环境变量
if [ -z "$JWT_SECRET_KEY" ]; then
    echo "错误: JWT_SECRET_KEY 未设置"
    exit 1
fi

if [ -z "$ZHIPU_API_KEY" ]; then
    echo "警告: ZHIPU_API_KEY 未设置，服务器将不启用 API 验证"
fi

# 初始化目录
mkdir -p /data
mkdir -p /models

# 运行初始化
echo "正在初始化..."
python setup_production.py

# 启动服务器
echo "启动 Gunicorn + Uvicorn..."
exec gunicorn server:app \
    -k uvicorn.workers.UvicornWorker \
    --bind 0.0.0.0:$PORT \
    --workers 2 \
    --threads 4 \
    --timeout 120 \
    --keep-alive 5 \
    --access-logfile - \
    --error-logfile -
