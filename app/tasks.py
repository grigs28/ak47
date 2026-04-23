from app import celery
from app.scan import Scanner
from app.models import ScanProgress


@celery.task(bind=True, max_retries=3)
def scan_task(self):
    """后台扫描任务"""
    scanner = Scanner()

    try:
        result = scanner.scan_all()
        return result
    except Exception as exc:
        # 如果暂停，不重试
        progress = ScanProgress.get()
        if progress['status'] == 'paused':
            return {'status': 'paused'}

        # 其他错误重试
        raise self.retry(exc=exc, countdown=10)
