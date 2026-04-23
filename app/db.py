import psycopg2
import psycopg2.extras
from flask import g, current_app

def get_conn():
    if 'db_conn' not in g:
        g.db_conn = psycopg2.connect(current_app.config['DB_DSN'])
        g.db_conn.autocommit = True
    return g.db_conn

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
    conn = g.pop('db_conn', None)
    if conn is not None:
        conn.close()
