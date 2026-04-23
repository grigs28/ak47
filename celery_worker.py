import os
# Celery worker 需要 DB_PASSWORD 环境变量
os.environ.setdefault('DB_PASSWORD', 'Slnwg123$')

from app import celery
from app.config import Config
from app import tasks  # noqa: F401 - 注册 scan_task

celery.conf.update(
    broker_url=Config.REDIS_URL,
    result_backend=Config.REDIS_URL,
    task_serializer='json',
    accept_content=['json'],
    result_serializer='json',
    timezone='Asia/Shanghai',
    enable_utc=True,
)
