from flask import Flask
from celery import Celery
from app.config import Config
from app.db import close_db

celery = Celery(__name__)

def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)

    celery.conf.update(
        broker_url=app.config['REDIS_URL'],
        result_backend=app.config['REDIS_URL'],
        task_serializer='json',
        accept_content=['json'],
        result_serializer='json',
        timezone='Asia/Shanghai',
        enable_utc=True,
    )

    app.teardown_appcontext(close_db)

    # 启动时检查：如果上次状态是 running，重置为 idle（程序异常退出）
    with app.app_context():
        from app.models import ScanProgress
        progress = ScanProgress.get()
        if progress and progress.get('status') == 'running':
            ScanProgress.update(status='idle')

    from app.views import bp as views_bp
    from app.api import bp as api_bp
    from app.auth import bp as auth_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(views_bp)
    app.register_blueprint(api_bp, url_prefix='/api')

    return app
