#!/bin/bash
set -e

echo "[$(date '+%Y-%m-%d %H:%M:%S')] Starting $1..."

# 根据命令执行
if [ "$1" = "web" ]; then
    exec gunicorn -b 0.0.0.0:5556 --timeout 120 --workers 2 run:app
elif [ "$1" = "worker" ]; then
    exec celery -A celery_worker worker -l info --pool=prefork --concurrency=$(nproc)
else
    exec "$@"
fi
