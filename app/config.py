import os

class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY', 'change-me-in-production')

    DB_HOST = os.environ.get('DB_HOST', '192.168.0.98')
    DB_PORT = int(os.environ.get('DB_PORT', 5432))
    DB_NAME = os.environ.get('DB_NAME', 'yz_relay')
    DB_USER = os.environ.get('DB_USER', 'grigs')
    DB_PASSWORD = os.environ.get('DB_PASSWORD', '')
    DB_DSN = f"host={DB_HOST} port={DB_PORT} dbname={DB_NAME} user={DB_USER} password='{DB_PASSWORD}'"

    # openGauss 数据库地址（业务数据）
    OG_HOST = os.environ.get('OG_HOST', '192.168.0.98')
    OG_PORT = int(os.environ.get('OG_PORT', '5432'))
    OG_NAME = os.environ.get('OG_NAME', 'yz_relay')
    OG_USER = os.environ.get('OG_USER', 'grigs')
    OG_PASS = os.environ.get('OG_PASS', '')

    REDIS_URL = os.environ.get('REDIS_URL', 'redis://localhost:6379/0')
    YZ_LOGIN_URL = os.environ.get('YZ_LOGIN_URL', 'http://192.168.0.18:5551')
    FLASK_ENV = os.environ.get('FLASK_ENV', 'development')

    # 管理员用户名列表（逗号分隔）
    ADMIN_USERS = os.environ.get('ADMIN_USERS', 'grigs').split(',')
