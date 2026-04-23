import psycopg2
import psycopg2.extras
from flask import g, current_app
from app.config import Config

def get_conn():
    # Celery worker 没有 app context，直接读 Config
    try:
        dsn = current_app.config['DB_DSN']
    except RuntimeError:
        dsn = Config.DB_DSN

    try:
        return g.db_conn
    except (AttributeError, RuntimeError):
        conn = psycopg2.connect(dsn)
        conn.autocommit = True
        return conn

def query(sql, params=None, fetchone=False, fetchall=False):
    conn = get_conn()
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(sql, params)
        if fetchone:
            return cur.fetchone()
        if fetchall:
            return cur.fetchall()
        return None

def execute(sql, params=None):
    conn = get_conn()
    with conn.cursor() as cur:
        cur.execute(sql, params)
    conn.commit()

def close_db(e=None):
    try:
        conn = g.pop('db_conn', None)
        if conn is not None:
            conn.close()
    except RuntimeError:
        pass
