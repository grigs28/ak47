from app import celery
from app.config import Config

celery.conf.update(
    broker_url=Config.REDIS_URL,
    result_backend=Config.REDIS_URL,
    task_serializer='json',
    accept_content=['json'],
    result_serializer='json',
    timezone='Asia/Shanghai',
    enable_utc=True,
)
