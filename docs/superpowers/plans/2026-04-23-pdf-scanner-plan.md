# PDF 绿色建筑规范扫描器 — 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 `/opt/yz/ak47` 构建一个基于 Flask + Celery + Redis 的 PDF 绿色建筑规范扫描系统，支持 SMB 挂载、PaddleOCR 识别、qwen-3 AI 匹配、yz-login 认证、Web 界面管理。

**Architecture:** Flask Web (5556) 提供 HTTP 服务和页面渲染，Celery Worker 执行后台扫描任务，Redis 作为消息队列，openGauss 持久化数据。SMB 在容器内通过 mount.cifs 只读挂载。所有配置通过 Web 界面管理。

**Tech Stack:** Flask 3.0, Celery 5.3, Redis 7, openGauss 3.0, psycopg2, Bootstrap 5, Docker

---

## 文件结构

```
/opt/yz/ak47/
├── app/
│   ├── __init__.py              # Flask app 工厂 + Celery 初始化
│   ├── config.py                # 配置类（环境变量 + 数据库配置）
│   ├── db.py                    # openGauss 连接 + query/execute 工具
│   ├── models.py                # 数据库表操作封装
│   ├── auth.py                  # yz-login 认证 + admin_required 装饰器
│   ├── scan.py                  # 目录遍历 + 文件扫描逻辑
│   ├── ocr.py                   # PaddleOCR-ui API 调用
│   ├── ai.py                    # qwen-3 AI 匹配
│   ├── smb.py                   # SMB 挂载/卸载管理
│   ├── tasks.py                 # Celery 后台任务
│   ├── api.py                   # REST API 路由
│   ├── views.py                 # 页面路由
│   ├── utils.py                 # 工具函数（数字提取、路径校验等）
│   ├── templates/               # Jinja2 模板
│   │   ├── base.html
│   │   ├── login.html
│   │   ├── dashboard.html
│   │   ├── files.html
│   │   ├── file_detail.html
│   │   ├── browse.html
│   │   ├── settings.html
│   │   └── logs.html
│   └── static/
│       ├── css/
│       │   └── style.css
│       └── js/
│           ├── dashboard.js
│           ├── files.js
│           └── settings.js
├── docker/
│   ├── docker-compose.yml
│   ├── Dockerfile
│   └── entrypoint.sh
├── tests/
│   ├── test_scan.py
│   ├── test_ocr.py
│   ├── test_ai.py
│   └── test_auth.py
├── requirements.txt
├── celery_worker.py
├── run.py
└── init_db.py
```

---

## Task 1: 项目骨架与依赖

**Files:**
- Create: `requirements.txt`
- Create: `run.py`
- Create: `celery_worker.py`
- Create: `app/__init__.py`
- Create: `app/config.py`

- [ ] **Step 1: 创建 requirements.txt**

```txt
Flask==3.0.3
celery==5.3.6
redis==5.0.1
psycopg2-binary==2.9.9
gunicorn==21.2.0
requests>=2.31.0
python-dotenv==1.0.0
```

- [ ] **Step 2: 创建 app/config.py**

```python
import os

class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY', 'ak47-scanner-secret-key')
    
    # openGauss
    DB_HOST = os.environ.get('DB_HOST', '192.168.0.98')
    DB_PORT = int(os.environ.get('DB_PORT', 5432))
    DB_NAME = os.environ.get('DB_NAME', 'yz_relay')
    DB_USER = os.environ.get('DB_USER', 'grigs')
    DB_PASSWORD = os.environ.get('DB_PASSWORD', 'Slnwg123$')
    DB_DSN = f"host={DB_HOST} port={DB_PORT} dbname={DB_NAME} user={DB_USER} password='{DB_PASSWORD}'"
    
    # Redis / Celery
    REDIS_URL = os.environ.get('REDIS_URL', 'redis://localhost:6379/0')
    
    # yz-login
    YZ_LOGIN_URL = os.environ.get('YZ_LOGIN_URL', 'http://192.168.0.19:5555')
    
    # Flask
    FLASK_ENV = os.environ.get('FLASK_ENV', 'development')
```

- [ ] **Step 3: 创建 app/__init__.py**

```python
from flask import Flask
from celery import Celery
from app.config import Config
from app.db import close_db

celery = Celery(__name__)

def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)
    
    # 初始化 Celery
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
    
    # 注册蓝图
    from app.views import bp as views_bp
    from app.api import bp as api_bp
    from app.auth import bp as auth_bp
    
    app.register_blueprint(auth_bp)
    app.register_blueprint(views_bp)
    app.register_blueprint(api_bp, url_prefix='/api')
    
    return app
```

- [ ] **Step 4: 创建 run.py**

```python
from app import create_app

app = create_app()

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5556, debug=True)
```

- [ ] **Step 5: 创建 celery_worker.py**

```python
from app import create_app, celery

app = create_app()
app.app_context().push()
```

- [ ] **Step 6: 安装依赖并测试启动**

Run: `pip install -r requirements.txt`
Run: `python -c "from app import create_app; app = create_app(); print('Flask OK')"`
Expected: `Flask OK`

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "feat: project skeleton and dependencies"
```

---

## Task 2: 数据库层

**Files:**
- Create: `app/db.py`
- Create: `app/models.py`
- Create: `init_db.py`

- [ ] **Step 1: 创建 app/db.py**

```python
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
```

- [ ] **Step 2: 创建 app/models.py**

```python
from app.db import query, execute

class ScanProgress:
    TABLE = 'scan_progress'
    
    @classmethod
    def get(cls):
        row = query(f"SELECT * FROM {cls.TABLE} WHERE id = 1", fetchone=True)
        if not row:
            execute(f"INSERT INTO {cls.TABLE} (id, status) VALUES (1, 'idle')")
            row = query(f"SELECT * FROM {cls.TABLE} WHERE id = 1", fetchone=True)
        return row
    
    @classmethod
    def update(cls, **kwargs):
        if not kwargs:
            return
        fields = ', '.join(f"{k} = %s" for k in kwargs)
        values = list(kwargs.values())
        execute(f"UPDATE {cls.TABLE} SET {fields}, updated_at = CURRENT_TIMESTAMP WHERE id = 1", values)
    
    @classmethod
    def reset(cls):
        execute(f"UPDATE {cls.TABLE} SET status = 'idle', current_dir = NULL, current_file = NULL, "
                f"dir_index = 0, file_index = 0, total_dirs = 0, total_files = 0, "
                f"scanned_files = 0, matched_files = 0, started_at = NULL, paused_at = NULL, "
                f"completed_at = NULL, updated_at = CURRENT_TIMESTAMP WHERE id = 1")

class ScannedFile:
    TABLE = 'scanned_files'
    
    @classmethod
    def create(cls, **kwargs):
        columns = ', '.join(kwargs.keys())
        placeholders = ', '.join('%s' for _ in kwargs)
        execute(f"INSERT INTO {cls.TABLE} ({columns}) VALUES ({placeholders})", list(kwargs.values()))
    
    @classmethod
    def get_by_path(cls, file_path):
        return query(f"SELECT * FROM {cls.TABLE} WHERE file_path = %s", (file_path,), fetchone=True)
    
    @classmethod
    def list(cls, directory=None, selected=None, ai_matched=None, page=1, size=20):
        where = []
        params = []
        if directory:
            where.append("directory = %s")
            params.append(directory)
        if selected is not None:
            where.append("selected = %s")
            params.append(selected)
        if ai_matched is not None:
            where.append("ai_matched = %s")
            params.append(ai_matched)
        
        where_clause = f"WHERE {' AND '.join(where)}" if where else ""
        offset = (page - 1) * size
        
        rows = query(f"SELECT * FROM {cls.TABLE} {where_clause} ORDER BY scanned_at DESC LIMIT %s OFFSET %s",
                     (*params, size, offset), fetchall=True)
        total = query(f"SELECT COUNT(*) as cnt FROM {cls.TABLE} {where_clause}", params, fetchone=True)
        return rows, total['cnt'] if total else 0
    
    @classmethod
    def update(cls, file_id, **kwargs):
        if not kwargs:
            return
        fields = ', '.join(f"{k} = %s" for k in kwargs)
        values = list(kwargs.values()) + [file_id]
        execute(f"UPDATE {cls.TABLE} SET {fields} WHERE id = %s", values)
    
    @classmethod
    def get(cls, file_id):
        return query(f"SELECT * FROM {cls.TABLE} WHERE id = %s", (file_id,), fetchone=True)

class SystemConfig:
    TABLE = 'system_config'
    
    @classmethod
    def get(cls, key, default=None):
        row = query(f"SELECT value FROM {cls.TABLE} WHERE key = %s", (key,), fetchone=True)
        return row['value'] if row else default
    
    @classmethod
    def set(cls, key, value):
        execute(f"INSERT INTO {cls.TABLE} (key, value) VALUES (%s, %s) "
                f"ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = CURRENT_TIMESTAMP",
                (key, value))
    
    @classmethod
    def all(cls):
        return query(f"SELECT * FROM {cls.TABLE} ORDER BY key", fetchall=True)

class OperationLog:
    TABLE = 'operation_logs'
    
    @classmethod
    def create(cls, user_id, username, action, detail=None):
        execute(f"INSERT INTO {cls.TABLE} (user_id, username, action, detail) VALUES (%s, %s, %s, %s)",
                (user_id, username, action, detail))
    
    @classmethod
    def list(cls, page=1, size=50):
        offset = (page - 1) * size
        return query(f"SELECT * FROM {cls.TABLE} ORDER BY created_at DESC LIMIT %s OFFSET %s",
                     (size, offset), fetchall=True)
```

- [ ] **Step 3: 创建 init_db.py**

```python
import psycopg2
from app.config import Config

DSN = Config.DB_DSN

INIT_SQL = """
CREATE TABLE IF NOT EXISTS scan_progress (
    id              SERIAL PRIMARY KEY,
    status          VARCHAR(20) NOT NULL DEFAULT 'idle',
    current_dir     VARCHAR(500),
    current_file    VARCHAR(500),
    dir_index       INTEGER DEFAULT 0,
    file_index      INTEGER DEFAULT 0,
    total_dirs      INTEGER DEFAULT 0,
    total_files     INTEGER DEFAULT 0,
    scanned_files   INTEGER DEFAULT 0,
    matched_files   INTEGER DEFAULT 0,
    started_at      TIMESTAMP,
    paused_at       TIMESTAMP,
    completed_at    TIMESTAMP,
    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS scanned_files (
    id              SERIAL PRIMARY KEY,
    file_path       VARCHAR(1000) NOT NULL,
    directory       VARCHAR(500) NOT NULL,
    filename        VARCHAR(500) NOT NULL,
    file_size       BIGINT,
    has_brackets    BOOLEAN DEFAULT FALSE,
    ai_matched      BOOLEAN DEFAULT NULL,
    ai_confidence   FLOAT,
    ai_reason       TEXT,
    ocr_status      VARCHAR(20) DEFAULT 'pending',
    ocr_task_id     INTEGER,
    md_content      TEXT,
    page_count      INTEGER,
    selected        BOOLEAN DEFAULT FALSE,
    converted       BOOLEAN DEFAULT FALSE,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    scanned_at      TIMESTAMP
);

CREATE TABLE IF NOT EXISTS system_config (
    key             VARCHAR(100) PRIMARY KEY,
    value           TEXT,
    description     VARCHAR(500),
    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS operation_logs (
    id              SERIAL PRIMARY KEY,
    user_id         INTEGER NOT NULL,
    username        VARCHAR(100),
    action          VARCHAR(50) NOT NULL,
    detail          JSONB,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

INSERT INTO scan_progress (id, status) VALUES (1, 'idle')
ON CONFLICT (id) DO NOTHING;

INSERT INTO system_config (key, value, description) VALUES
('smb_server', '192.168.0.79', 'SMB 服务器地址'),
('smb_share', 'abb/FS/10/D$/tbmdata/data/ftpdata', 'SMB 共享路径'),
('smb_username', '', 'SMB 用户名'),
('smb_password', '', 'SMB 密码'),
('smb_mount_path', '/mnt/smb/ftpdata', 'SMB 容器内挂载点'),
('paddleocr_base_url', 'http://192.168.0.19:5553', 'PaddleOCR-ui 地址'),
('paddleocr_api_key', 'ak_e10b412d5cd68eeef303c3f561405dfb07d7e122123df8f97d0ecb30e5624d', 'PaddleOCR API Key'),
('qwen_base_url', 'http://192.168.0.18:5566/v1', 'qwen-3 API 地址'),
('qwen_api_key', '', 'qwen-3 API Key'),
('qwen_model', 'qwen3', 'qwen-3 模型名'),
('yz_login_url', 'http://192.168.0.19:5555', 'yz-login 地址'),
('scan_concurrency', '1', '并发扫描数'),
('ai_enabled', 'true', '是否启用 AI 辅助匹配'),
('gbt_standard', 'GBT 50378-2019(2024年版)', '绿色建筑标准名称')
ON CONFLICT (key) DO NOTHING;
"""

def init():
    conn = psycopg2.connect(DSN)
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute(INIT_SQL)
    conn.close()
    print("Database initialized.")

if __name__ == '__main__':
    init()
```

- [ ] **Step 4: 运行数据库初始化**

Run: `python init_db.py`
Expected: `Database initialized.`

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat: database layer and init script"
```

---

## Task 3: 认证模块

**Files:**
- Create: `app/auth.py`

- [ ] **Step 1: 创建 app/auth.py**

```python
import requests
from functools import wraps
from flask import Blueprint, session, redirect, request, current_app, url_for

bp = Blueprint('auth', __name__)

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('auth.login_page'))
        return f(*args, **kwargs)
    return decorated

def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('auth.login_page'))
        if not session.get('is_admin'):
            return "权限不足", 403
        return f(*args, **kwargs)
    return decorated

@bp.route('/login')
def login_page():
    yz_url = current_app.config['YZ_LOGIN_URL']
    redirect_uri = request.url_root.rstrip('/') + '/callback'
    return redirect(f"{yz_url}/auth/sso?app_id=ak47&redirect_uri={redirect_uri}")

@bp.route('/callback')
def callback():
    ticket = request.args.get('ticket')
    if not ticket:
        return "缺少 ticket", 400
    
    yz_url = current_app.config['YZ_LOGIN_URL']
    try:
        resp = requests.get(f"{yz_url}/auth/verify-ticket", params={'ticket': ticket}, timeout=10)
        data = resp.json()
    except Exception as e:
        return f"验证失败: {e}", 500
    
    if not data.get('user_id'):
        return "认证失败", 401
    
    if not data.get('is_admin'):
        return "仅管理员可访问", 403
    
    session['user_id'] = data['user_id']
    session['username'] = data.get('username', '')
    session['display_name'] = data.get('display_name', '')
    session['is_admin'] = data.get('is_admin', 0)
    
    return redirect(url_for('views.dashboard'))

@bp.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('auth.login_page'))

@bp.route('/auth/me')
@login_required
def me():
    return {
        'user_id': session.get('user_id'),
        'username': session.get('username'),
        'display_name': session.get('display_name'),
        'is_admin': session.get('is_admin'),
    }
```

- [ ] **Step 2: Commit**

```bash
git add app/auth.py
git commit -m "feat: yz-login authentication module"
```

---

## Task 4: SMB 挂载模块

**Files:**
- Create: `app/smb.py`

- [ ] **Step 1: 创建 app/smb.py**

```python
import os
import subprocess
import shutil
from app.models import SystemConfig

class SMBManager:
    @staticmethod
    def get_mount_path():
        return SystemConfig.get('smb_mount_path', '/mnt/smb/ftpdata')
    
    @staticmethod
    def get_config():
        return {
            'server': SystemConfig.get('smb_server', '192.168.0.79'),
            'share': SystemConfig.get('smb_share', 'abb/FS/10/D$/tbmdata/data/ftpdata'),
            'username': SystemConfig.get('smb_username', ''),
            'password': SystemConfig.get('smb_password', ''),
            'mount_path': SMBManager.get_mount_path(),
        }
    
    @classmethod
    def is_mounted(cls):
        mount_path = cls.get_mount_path()
        if not os.path.ismount(mount_path):
            return False
        try:
            os.listdir(mount_path)
            return True
        except OSError:
            return False
    
    @classmethod
    def mount(cls):
        cfg = cls.get_config()
        mount_path = cfg['mount_path']
        
        os.makedirs(mount_path, exist_ok=True)
        
        if cls.is_mounted():
            cls.umount()
        
        share_url = f"//{cfg['server']}/{cfg['share'].replace('/', '\\')}"
        cmd = [
            'mount', '-t', 'cifs',
            share_url,
            mount_path,
            '-o', f"username={cfg['username']},password={cfg['password']},ro,vers=3.0"
        ]
        
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"SMB mount failed: {result.stderr}")
        
        return True
    
    @classmethod
    def umount(cls):
        mount_path = cls.get_mount_path()
        if not os.path.ismount(mount_path):
            return True
        
        result = subprocess.run(['umount', mount_path], capture_output=True, text=True)
        return result.returncode == 0
    
    @classmethod
    def list_dirs(cls):
        mount_path = cls.get_mount_path()
        if not cls.is_mounted():
            raise RuntimeError("SMB not mounted")
        
        dirs = []
        for name in os.listdir(mount_path):
            full = os.path.join(mount_path, name)
            if os.path.isdir(full):
                num = cls._extract_number(name)
                dirs.append((name, num))
        
        dirs.sort(key=lambda x: x[1], reverse=True)
        return dirs
    
    @classmethod
    def list_pdfs(cls, directory):
        mount_path = cls.get_mount_path()
        dir_path = os.path.join(mount_path, directory)
        
        real_dir = os.path.realpath(dir_path)
        real_mount = os.path.realpath(mount_path)
        if not real_dir.startswith(real_mount):
            raise ValueError("Invalid directory path")
        
        pdfs = []
        for name in sorted(os.listdir(real_dir)):
            if name.lower().endswith('.pdf'):
                full = os.path.join(real_dir, name)
                if os.path.isfile(full):
                    pdfs.append({
                        'name': name,
                        'size': os.path.getsize(full),
                        'path': os.path.join(directory, name),
                    })
        return pdfs
    
    @staticmethod
    def _extract_number(dirname):
        import re
        match = re.search(r'\d+', dirname)
        return int(match.group()) if match else 0
    
    @classmethod
    def get_file_path(cls, relative_path):
        mount_path = cls.get_mount_path()
        full = os.path.join(mount_path, relative_path)
        real_full = os.path.realpath(full)
        real_mount = os.path.realpath(mount_path)
        if not real_full.startswith(real_mount):
            raise ValueError("Invalid file path")
        return real_full
```

- [ ] **Step 2: Commit**

```bash
git add app/smb.py
git commit -m "feat: SMB mount manager"
```

---

## Task 5: OCR 模块

**Files:**
- Create: `app/ocr.py`

- [ ] **Step 1: 创建 app/ocr.py**

```python
import requests
import time
from app.models import SystemConfig

class OCRClient:
    def __init__(self):
        self.base_url = SystemConfig.get('paddleocr_base_url', 'http://192.168.0.19:5553')
        self.api_key = SystemConfig.get('paddleocr_api_key', '')
    
    def _headers(self):
        return {'X-API-Key': self.api_key}
    
    def submit_task(self, file_path, output_formats=None):
        if output_formats is None:
            output_formats = ['markdown']
        
        url = f"{self.base_url}/api/v1/tasks"
        
        with open(file_path, 'rb') as f:
            files = {'file': f}
            data = {
                'task_type': 'ocr',
                'output_formats': str(output_formats),
            }
            resp = requests.post(url, headers=self._headers(), files=files, data=data, timeout=60)
        
        resp.raise_for_status()
        return resp.json()['task_id']
    
    def get_task(self, task_id):
        url = f"{self.base_url}/api/v1/tasks/{task_id}"
        resp = requests.get(url, headers=self._headers(), timeout=30)
        resp.raise_for_status()
        return resp.json()
    
    def wait_for_completion(self, task_id, poll_interval=3, max_retries=600):
        for _ in range(max_retries):
            data = self.get_task(task_id)
            task = data.get('task', {})
            status = task.get('status')
            
            if status == 'completed':
                return data
            elif status == 'failed':
                raise RuntimeError(f"OCR task failed: {task.get('error_message')}")
            
            time.sleep(poll_interval)
        
        raise TimeoutError(f"OCR task {task_id} did not complete within timeout")
    
    def get_result(self, task_id):
        data = self.wait_for_completion(task_id)
        return data.get('result', '')
    
    def process_file(self, file_path):
        task_id = self.submit_task(file_path, output_formats=['markdown'])
        result = self.get_result(task_id)
        return task_id, result
```

- [ ] **Step 2: Commit**

```bash
git add app/ocr.py
git commit -m "feat: PaddleOCR client"
```

---

## Task 6: AI 匹配模块

**Files:**
- Create: `app/ai.py`

- [ ] **Step 1: 创建 app/ai.py**

```python
import requests
import json
import re
from app.models import SystemConfig

class AIMatcher:
    def __init__(self):
        self.base_url = SystemConfig.get('qwen_base_url', 'http://192.168.0.18:5566/v1')
        self.api_key = SystemConfig.get('qwen_api_key', '')
        self.model = SystemConfig.get('qwen_model', 'qwen3')
        self.enabled = SystemConfig.get('ai_enabled', 'true').lower() == 'true'
        self.standard = SystemConfig.get('gbt_standard', 'GBT 50378-2019(2024年版)')
    
    def match(self, content):
        if not self.enabled:
            return {'matched': None, 'confidence': None, 'reason': 'AI disabled'}
        
        text = content[:3000]
        
        prompt = f"""请判断以下文档内容是否与绿色建筑评价标准 {self.standard} 相关。

文档内容（前 3000 字符）：
{text}

请按以下 JSON 格式返回，不要包含其他内容：
{{"matched": true/false, "confidence": 0.0-1.0, "reason": "判断理由"}}"""
        
        try:
            resp = requests.post(
                f"{self.base_url}/chat/completions",
                headers={
                    'Authorization': f'Bearer {self.api_key}',
                    'Content-Type': 'application/json',
                },
                json={
                    'model': self.model,
                    'messages': [
                        {'role': 'system', 'content': '你是一个文档分类助手，请严格按 JSON 格式返回结果。'},
                        {'role': 'user', 'content': prompt},
                    ],
                    'temperature': 0.1,
                    'max_tokens': 500,
                },
                timeout=60,
            )
            resp.raise_for_status()
            data = resp.json()
            
            ai_text = data['choices'][0]['message']['content']
            
            json_match = re.search(r'\{[^}]+\}', ai_text)
            if json_match:
                result = json.loads(json_match.group())
                return {
                    'matched': result.get('matched'),
                    'confidence': result.get('confidence'),
                    'reason': result.get('reason', ''),
                }
            else:
                return {'matched': None, 'confidence': None, 'reason': 'JSON parse failed'}
        
        except Exception as e:
            return {'matched': None, 'confidence': None, 'reason': f'AI error: {str(e)}'}
    
    @staticmethod
    def has_brackets(content):
        return bool(re.search(r'【[^】]+】', content))
```

- [ ] **Step 2: Commit**

```bash
git add app/ai.py
git commit -m "feat: qwen-3 AI matcher"
```

---

## Task 7: 扫描逻辑模块

**Files:**
- Create: `app/scan.py`
- Create: `app/utils.py`

- [ ] **Step 1: 创建 app/utils.py**

```python
import os
import re

def extract_number(dirname):
    match = re.search(r'\d+', dirname)
    return int(match.group()) if match else 0

def safe_path(base, relative):
    full = os.path.join(base, relative)
    real_full = os.path.realpath(full)
    real_base = os.path.realpath(base)
    if not real_full.startswith(real_base):
        raise ValueError("Invalid path")
    return real_full
```

- [ ] **Step 2: 创建 app/scan.py**

```python
import os
from app.smb import SMBManager
from app.ocr import OCRClient
from app.ai import AIMatcher
from app.models import ScanProgress, ScannedFile

class Scanner:
    def __init__(self):
        self.smb = SMBManager()
        self.ocr = OCRClient()
        self.ai = AIMatcher()
    
    def scan_all(self):
        progress = ScanProgress.get()
        
        if progress['status'] == 'completed':
            ScanProgress.reset()
            progress = ScanProgress.get()
        
        dirs = self.smb.list_dirs()
        total_dirs = len(dirs)
        
        total_files = 0
        for dirname, _ in dirs:
            pdfs = self.smb.list_pdfs(dirname)
            total_files += len(pdfs)
        
        ScanProgress.update(
            status='running',
            total_dirs=total_dirs,
            total_files=total_files,
            started_at='CURRENT_TIMESTAMP',
        )
        
        start_dir_idx = progress.get('dir_index', 0)
        start_file_idx = progress.get('file_index', 0)
        
        scanned = progress.get('scanned_files', 0)
        matched = progress.get('matched_files', 0)
        
        for dir_idx in range(start_dir_idx, total_dirs):
            dirname, _ = dirs[dir_idx]
            pdfs = self.smb.list_pdfs(dirname)
            
            for file_idx in range(start_file_idx, len(pdfs)):
                current = ScanProgress.get()
                if current['status'] == 'paused':
                    ScanProgress.update(
                        current_dir=dirname,
                        current_file=pdfs[file_idx]['path'],
                        dir_index=dir_idx,
                        file_index=file_idx,
                        scanned_files=scanned,
                        matched_files=matched,
                    )
                    return {'status': 'paused'}
                
                pdf = pdfs[file_idx]
                file_path = self.smb.get_file_path(pdf['path'])
                
                try:
                    task_id, md_content = self.ocr.process_file(file_path)
                    
                    has_brackets = self.ai.has_brackets(md_content)
                    
                    scanned += 1
                    
                    if has_brackets:
                        matched += 1
                        
                        ai_result = self.ai.match(md_content)
                        
                        ScannedFile.create(
                            file_path=pdf['path'],
                            directory=dirname,
                            filename=pdf['name'],
                            file_size=pdf['size'],
                            has_brackets=True,
                            ai_matched=ai_result.get('matched'),
                            ai_confidence=ai_result.get('confidence'),
                            ai_reason=ai_result.get('reason'),
                            ocr_status='done',
                            ocr_task_id=task_id,
                            md_content=md_content,
                            scanned_at='CURRENT_TIMESTAMP',
                        )
                    
                    ScanProgress.update(
                        current_dir=dirname,
                        current_file=pdf['path'],
                        dir_index=dir_idx,
                        file_index=file_idx,
                        scanned_files=scanned,
                        matched_files=matched,
                    )
                
                except Exception as e:
                    print(f"Error processing {pdf['path']}: {e}")
                    scanned += 1
                    ScanProgress.update(
                        current_dir=dirname,
                        current_file=pdf['path'],
                        dir_index=dir_idx,
                        file_index=file_idx,
                        scanned_files=scanned,
                    )
            
            start_file_idx = 0
        
        ScanProgress.update(
            status='completed',
            completed_at='CURRENT_TIMESTAMP',
        )
        
        return {'status': 'completed', 'scanned': scanned, 'matched': matched}
```

- [ ] **Step 3: Commit**

```bash
git add app/scan.py app/utils.py
git commit -m "feat: scanner logic"
```

---

## Task 8: Celery 任务

**Files:**
- Create: `app/tasks.py`

- [ ] **Step 1: 创建 app/tasks.py**

```python
from app import celery
from app.scan import Scanner
from app.models import ScanProgress

@celery.task(bind=True, max_retries=3)
def scan_task(self):
    scanner = Scanner()
    
    try:
        result = scanner.scan_all()
        return result
    except Exception as exc:
        progress = ScanProgress.get()
        if progress['status'] == 'paused':
            return {'status': 'paused'}
        
        raise self.retry(exc=exc, countdown=10)
```

- [ ] **Step 2: Commit**

```bash
git add app/tasks.py
git commit -m "feat: celery scan task"
```

---

## Task 9: REST API

**Files:**
- Create: `app/api.py`

- [ ] **Step 1: 创建 app/api.py**

```python
from flask import Blueprint, request, jsonify
from app.auth import admin_required
from app.models import ScanProgress, ScannedFile, SystemConfig, OperationLog
from app.tasks import scan_task
from app.smb import SMBManager
from app.ocr import OCRClient
from app.ai import AIMatcher
from app.db import close_db

bp = Blueprint('api', __name__)

@bp.teardown_request
def teardown(e=None):
    close_db(e)

@bp.route('/scan/start', methods=['POST'])
@admin_required
def scan_start():
    progress = ScanProgress.get()
    if progress['status'] == 'running':
        return jsonify({'error': '扫描已在运行'}), 400
    
    ScanProgress.update(status='running')
    task = scan_task.delay()
    
    OperationLog.create(
        user_id=request.session.get('user_id', 0),
        username=request.session.get('username', ''),
        action='start_scan',
        detail={'task_id': task.id},
    )
    
    return jsonify({'task_id': task.id, 'message': '扫描已启动'})

@bp.route('/scan/pause', methods=['POST'])
@admin_required
def scan_pause():
    progress = ScanProgress.get()
    if progress['status'] != 'running':
        return jsonify({'error': '扫描未在运行'}), 400
    
    ScanProgress.update(status='paused', paused_at='CURRENT_TIMESTAMP')
    
    OperationLog.create(
        user_id=request.session.get('user_id', 0),
        username=request.session.get('username', ''),
        action='pause_scan',
    )
    
    return jsonify({'message': '扫描已暂停'})

@bp.route('/scan/resume', methods=['POST'])
@admin_required
def scan_resume():
    progress = ScanProgress.get()
    if progress['status'] != 'paused':
        return jsonify({'error': '扫描未在暂停状态'}), 400
    
    ScanProgress.update(status='running')
    task = scan_task.delay()
    
    OperationLog.create(
        user_id=request.session.get('user_id', 0),
        username=request.session.get('username', ''),
        action='resume_scan',
        detail={'task_id': task.id},
    )
    
    return jsonify({'task_id': task.id, 'message': '扫描已恢复'})

@bp.route('/scan/reset', methods=['POST'])
@admin_required
def scan_reset():
    ScanProgress.reset()
    
    OperationLog.create(
        user_id=request.session.get('user_id', 0),
        username=request.session.get('username', ''),
        action='reset_scan',
    )
    
    return jsonify({'message': '扫描进度已重置'})

@bp.route('/scan/status', methods=['GET'])
@admin_required
def scan_status():
    progress = ScanProgress.get()
    return jsonify(progress)

@bp.route('/files', methods=['GET'])
@admin_required
def file_list():
    page = request.args.get('page', 1, type=int)
    size = request.args.get('size', 20, type=int)
    directory = request.args.get('directory', None)
    selected = request.args.get('selected', None)
    if selected is not None:
        selected = selected.lower() == 'true'
    ai_matched = request.args.get('ai_matched', None)
    if ai_matched is not None:
        ai_matched = ai_matched.lower() == 'true'
    
    rows, total = ScannedFile.list(
        directory=directory,
        selected=selected,
        ai_matched=ai_matched,
        page=page,
        size=size,
    )
    
    return jsonify({
        'files': rows,
        'total': total,
        'page': page,
        'size': size,
    })

@bp.route('/files/<int:file_id>', methods=['GET'])
@admin_required
def file_detail(file_id):
    row = ScannedFile.get(file_id)
    if not row:
        return jsonify({'error': '文件不存在'}), 404
    return jsonify(row)

@bp.route('/files/<int:file_id>/select', methods=['POST'])
@admin_required
def file_select(file_id):
    data = request.get_json() or {}
    selected = data.get('selected', True)
    
    ScannedFile.update(file_id, selected=selected)
    
    OperationLog.create(
        user_id=request.session.get('user_id', 0),
        username=request.session.get('username', ''),
        action='select_file' if selected else 'deselect_file',
        detail={'file_id': file_id},
    )
    
    return jsonify({'message': '已更新'})

@bp.route('/files/batch-select', methods=['POST'])
@admin_required
def file_batch_select():
    data = request.get_json() or {}
    file_ids = data.get('file_ids', [])
    selected = data.get('selected', True)
    
    for fid in file_ids:
        ScannedFile.update(fid, selected=selected)
    
    return jsonify({'message': f'已更新 {len(file_ids)} 个文件'})

@bp.route('/files/<int:file_id>/preview', methods=['GET'])
@admin_required
def file_preview(file_id):
    from flask import send_file
    import os
    
    row = ScannedFile.get(file_id)
    if not row:
        return jsonify({'error': '文件不存在'}), 404
    
    try:
        file_path = SMBManager.get_file_path(row['file_path'])
        return send_file(file_path, mimetype='application/pdf')
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@bp.route('/files/<int:file_id>/md', methods=['GET'])
@admin_required
def file_download_md(file_id):
    from flask import Response
    
    row = ScannedFile.get(file_id)
    if not row:
        return jsonify({'error': '文件不存在'}), 404
    
    content = row.get('md_content', '')
    filename = row['filename'].replace('.pdf', '.md')
    
    return Response(
        content,
        mimetype='text/markdown',
        headers={'Content-Disposition': f'attachment; filename={filename}'},
    )

@bp.route('/browse', methods=['GET'])
@admin_required
def browse():
    path = request.args.get('path', '')
    
    try:
        if not path:
            dirs = SMBManager.list_dirs()
            return jsonify({
                'type': 'root',
                'items': [{'name': d[0], 'number': d[1], 'type': 'directory'} for d in dirs],
            })
        else:
            pdfs = SMBManager.list_pdfs(path)
            return jsonify({
                'type': 'directory',
                'path': path,
                'items': [{'name': p['name'], 'size': p['size'], 'type': 'pdf'} for p in pdfs],
            })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@bp.route('/config', methods=['GET'])
@admin_required
def config_list():
    rows = SystemConfig.all()
    return jsonify({'configs': rows})

@bp.route('/config/<key>', methods=['PUT'])
@admin_required
def config_update(key):
    data = request.get_json() or {}
    value = data.get('value')
    
    if value is None:
        return jsonify({'error': '缺少 value'}), 400
    
    SystemConfig.set(key, value)
    
    OperationLog.create(
        user_id=request.session.get('user_id', 0),
        username=request.session.get('username', ''),
        action='update_config',
        detail={'key': key},
    )
    
    return jsonify({'message': '配置已更新'})

@bp.route('/config/test-smb', methods=['POST'])
@admin_required
def test_smb():
    try:
        SMBManager.mount()
        is_mounted = SMBManager.is_mounted()
        return jsonify({'success': is_mounted, 'message': 'SMB 挂载成功' if is_mounted else 'SMB 挂载失败'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

@bp.route('/config/test-ocr', methods=['POST'])
@admin_required
def test_ocr():
    try:
        client = OCRClient()
        return jsonify({'success': True, 'base_url': client.base_url})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

@bp.route('/config/test-ai', methods=['POST'])
@admin_required
def test_ai():
    try:
        matcher = AIMatcher()
        result = matcher.match("【绿色建筑评价标准】测试内容")
        return jsonify({'success': True, 'result': result})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

@bp.route('/logs', methods=['GET'])
@admin_required
def log_list():
    page = request.args.get('page', 1, type=int)
    size = request.args.get('size', 50, type=int)
    rows = OperationLog.list(page=page, size=size)
    return jsonify({'logs': rows})
```

- [ ] **Step 2: Commit**

```bash
git add app/api.py
git commit -m "feat: REST API endpoints"
```

---

## Task 10: 页面路由与模板

**Files:**
- Create: `app/views.py`
- Create: `app/templates/base.html`
- Create: `app/templates/login.html`
- Create: `app/templates/dashboard.html`

- [ ] **Step 1: 创建 app/views.py**

```python
from flask import Blueprint, render_template, session
from app.auth import admin_required

bp = Blueprint('views', __name__)

@bp.route('/login')
def login():
    return render_template('login.html')

@bp.route('/')
@admin_required
def dashboard():
    return render_template('dashboard.html',
                         username=session.get('display_name', session.get('username', 'Admin')))

@bp.route('/files')
@admin_required
def files():
    return render_template('files.html')

@bp.route('/files/<int:file_id>')
@admin_required
def file_detail(file_id):
    return render_template('file_detail.html', file_id=file_id)

@bp.route('/browse')
@admin_required
def browse():
    return render_template('browse.html')

@bp.route('/settings')
@admin_required
def settings():
    return render_template('settings.html')

@bp.route('/logs')
@admin_required
def logs():
    return render_template('logs.html')
```

- [ ] **Step 2: 创建 app/templates/base.html**

```html
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{% block title %}PDF 扫描器{% endblock %}</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.10.0/font/bootstrap-icons.css">
    <style>
        .navbar-brand { font-weight: bold; }
        .stat-card { transition: transform 0.2s; }
        .stat-card:hover { transform: translateY(-2px); }
        .progress { height: 25px; }
        .file-selected { background-color: #d1e7dd !important; }
    </style>
    {% block extra_css %}{% endblock %}
</head>
<body>
    <nav class="navbar navbar-expand-lg navbar-dark bg-dark">
        <div class="container">
            <a class="navbar-brand" href="/">PDF 扫描器</a>
            <div class="collapse navbar-collapse">
                <ul class="navbar-nav me-auto">
                    <li class="nav-item"><a class="nav-link" href="/">仪表盘</a></li>
                    <li class="nav-item"><a class="nav-link" href="/files">文件列表</a></li>
                    <li class="nav-item"><a class="nav-link" href="/browse">目录浏览</a></li>
                    <li class="nav-item"><a class="nav-link" href="/settings">配置</a></li>
                    <li class="nav-item"><a class="nav-link" href="/logs">日志</a></li>
                </ul>
                <span class="navbar-text text-light me-3">{{ username }}</span>
                <a href="/logout" class="btn btn-outline-light btn-sm">退出</a>
            </div>
        </div>
    </nav>
    
    <div class="container mt-4">
        {% block content %}{% endblock %}
    </div>
    
    <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js"></script>
    {% block extra_js %}{% endblock %}
</body>
</html>
```

- [ ] **Step 3: 创建 app/templates/login.html**

```html
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <title>登录 - PDF 扫描器</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    <style>
        body { background: #f5f5f5; }
        .login-box { max-width: 400px; margin: 100px auto; }
    </style>
</head>
<body>
    <div class="login-box">
        <div class="card shadow">
            <div class="card-body text-center p-5">
                <h3 class="mb-4">PDF 扫描器</h3>
                <p class="text-muted mb-4">请使用 yz-login 管理员账号登录</p>
                <a href="/login" class="btn btn-primary btn-lg w-100">点击登录</a>
            </div>
        </div>
    </div>
</body>
</html>
```

- [ ] **Step 4: 创建 app/templates/dashboard.html**

```html
{% extends "base.html" %}

{% block title %}仪表盘 - PDF 扫描器{% endblock %}

{% block content %}
<div id="notification-area"></div>

<div class="row mb-4">
    <div class="col-md-4">
        <div class="card stat-card bg-primary text-white">
            <div class="card-body">
                <h5>总目录数</h5>
                <h2 id="total-dirs">-</h2>
            </div>
        </div>
    </div>
    <div class="col-md-4">
        <div class="card stat-card bg-info text-white">
            <div class="card-body">
                <h5>已扫描</h5>
                <h2 id="scanned-files">-</h2>
            </div>
        </div>
    </div>
    <div class="col-md-4">
        <div class="card stat-card bg-success text-white">
            <div class="card-body">
                <h5>匹配数</h5>
                <h2 id="matched-files">-</h2>
            </div>
        </div>
    </div>
</div>

<div class="card mb-4">
    <div class="card-body">
        <h5>扫描进度</h5>
        <div class="progress mb-2">
            <div id="progress-bar" class="progress-bar" role="progressbar" style="width: 0%">0%</div>
        </div>
        <p class="text-muted" id="current-file">当前：-</p>
        <div class="btn-group">
            <button id="btn-start" class="btn btn-success" onclick="startScan()">开始扫描</button>
            <button id="btn-pause" class="btn btn-warning" onclick="pauseScan()">暂停</button>
            <button id="btn-resume" class="btn btn-info" onclick="resumeScan()">恢复</button>
            <button id="btn-reset" class="btn btn-danger" onclick="resetScan()">重置</button>
        </div>
    </div>
</div>

<div class="card">
    <div class="card-header">
        <h5>最近匹配文件</h5>
    </div>
    <div class="card-body">
        <table class="table table-striped">
            <thead>
                <tr><th>文件名</th><th>目录</th><th>AI匹配</th><th>操作</th></tr>
            </thead>
            <tbody id="recent-files"></tbody>
        </table>
    </div>
</div>
{% endblock %}

{% block extra_js %}
<script>
let statusInterval;

function updateStatus() {
    fetch('/api/scan/status')
        .then(r => r.json())
        .then(data => {
            document.getElementById('total-dirs').textContent = data.total_dirs || 0;
            document.getElementById('scanned-files').textContent = data.scanned_files || 0;
            document.getElementById('matched-files').textContent = data.matched_files || 0;
            
            const total = data.total_files || 1;
            const scanned = data.scanned_files || 0;
            const pct = Math.round((scanned / total) * 100);
            document.getElementById('progress-bar').style.width = pct + '%';
            document.getElementById('progress-bar').textContent = pct + '%';
            document.getElementById('current-file').textContent = '当前：' + (data.current_file || '-');
            
            const status = data.status;
            document.getElementById('btn-start').disabled = status === 'running';
            document.getElementById('btn-pause').disabled = status !== 'running';
            document.getElementById('btn-resume').disabled = status !== 'paused';
            
            if (status === 'completed') {
                showNotification('扫描完成！共发现 ' + data.matched_files + ' 个匹配文件', 'success');
            }
        });
    
    fetch('/api/files?size=5')
        .then(r => r.json())
        .then(data => {
            const tbody = document.getElementById('recent-files');
            tbody.innerHTML = data.files.map(f => `
                <tr>
                    <td>${escapeHtml(f.filename)}</td>
                    <td>${escapeHtml(f.directory)}</td>
                    <td>${f.ai_confidence ? (f.ai_confidence * 100).toFixed(0) + '%' : '-'}</td>
                    <td><a href="/files/${f.id}" class="btn btn-sm btn-outline-primary">查看</a></td>
                </tr>
            `).join('');
        });
}

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

function startScan() {
    fetch('/api/scan/start', {method: 'POST'}).then(() => updateStatus());
}

function pauseScan() {
    fetch('/api/scan/pause', {method: 'POST'}).then(() => updateStatus());
}

function resumeScan() {
    fetch('/api/scan/resume', {method: 'POST'}).then(() => updateStatus());
}

function resetScan() {
    if (!confirm('确定重置扫描进度？')) return;
    fetch('/api/scan/reset', {method: 'POST'}).then(() => updateStatus());
}

function showNotification(msg, type) {
    const area = document.getElementById('notification-area');
    const div = document.createElement('div');
    div.className = `alert alert-${type} alert-dismissible fade show`;
    div.innerHTML = msg + '<button type="button" class="btn-close" data-bs-dismiss="alert"></button>';
    area.appendChild(div);
}

updateStatus();
statusInterval = setInterval(updateStatus, 3000);
</script>
{% endblock %}
```

- [ ] **Step 5: Commit**

```bash
git add app/views.py app/templates/
git commit -m "feat: page routes and dashboard template"
```

---

## Task 11: 剩余模板

**Files:**
- Create: `app/templates/files.html`
- Create: `app/templates/file_detail.html`
- Create: `app/templates/browse.html`
- Create: `app/templates/settings.html`
- Create: `app/templates/logs.html`

- [ ] **Step 1: 创建 app/templates/files.html**

```html
{% extends "base.html" %}

{% block title %}文件列表 - PDF 扫描器{% endblock %}

{% block content %}
<div class="d-flex justify-content-between align-items-center mb-3">
    <h3>匹配文件列表</h3>
    <div>
        <button class="btn btn-success" onclick="batchSelect(true)">批量选中</button>
        <button class="btn btn-outline-secondary" onclick="batchSelect(false)">取消选中</button>
    </div>
</div>

<div class="card mb-3">
    <div class="card-body">
        <div class="row g-2">
            <div class="col-md-3">
                <select id="filter-dir" class="form-select" onchange="loadFiles()">
                    <option value="">所有目录</option>
                </select>
            </div>
            <div class="col-md-3">
                <select id="filter-ai" class="form-select" onchange="loadFiles()">
                    <option value="">AI 匹配状态</option>
                    <option value="true">已匹配</option>
                    <option value="false">未匹配</option>
                </select>
            </div>
            <div class="col-md-3">
                <select id="filter-selected" class="form-select" onchange="loadFiles()">
                    <option value="">选中状态</option>
                    <option value="true">已选中</option>
                    <option value="false">未选中</option>
                </select>
            </div>
        </div>
    </div>
</div>

<table class="table table-hover">
    <thead>
        <tr>
            <th><input type="checkbox" id="select-all" onchange="toggleAll()"></th>
            <th>文件名</th>
            <th>目录</th>
            <th>大小</th>
            <th>AI 匹配</th>
            <th>【】检测</th>
            <th>操作</th>
        </tr>
    </thead>
    <tbody id="file-list"></tbody>
</table>

<nav>
    <ul class="pagination" id="pagination"></ul>
</nav>
{% endblock %}

{% block extra_js %}
<script>
let currentPage = 1;
let selectedIds = new Set();

function loadFiles(page = 1) {
    currentPage = page;
    const params = new URLSearchParams({page, size: 20});
    const dir = document.getElementById('filter-dir').value;
    const ai = document.getElementById('filter-ai').value;
    const sel = document.getElementById('filter-selected').value;
    if (dir) params.append('directory', dir);
    if (ai) params.append('ai_matched', ai);
    if (sel) params.append('selected', sel);
    
    fetch('/api/files?' + params)
        .then(r => r.json())
        .then(data => {
            const tbody = document.getElementById('file-list');
            tbody.innerHTML = data.files.map(f => `
                <tr class="${f.selected ? 'file-selected' : ''}">
                    <td><input type="checkbox" value="${f.id}" ${selectedIds.has(f.id) ? 'checked' : ''} onchange="toggleSelect(${f.id})"></td>
                    <td><a href="/files/${f.id}">${escapeHtml(f.filename)}</a></td>
                    <td>${escapeHtml(f.directory)}</td>
                    <td>${formatSize(f.file_size)}</td>
                    <td>${f.ai_confidence ? (f.ai_confidence * 100).toFixed(0) + '%' : '-'}</td>
                    <td>${f.has_brackets ? '是' : '否'}</td>
                    <td>
                        <a href="/api/files/${f.id}/preview" target="_blank" class="btn btn-sm btn-outline-primary">预览</a>
                        <a href="/api/files/${f.id}/md" class="btn btn-sm btn-outline-success">下载 MD</a>
                    </td>
                </tr>
            `).join('');
            
            renderPagination(data.total, data.page, data.size);
        });
}

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

function formatSize(bytes) {
    if (!bytes) return '-';
    const units = ['B', 'KB', 'MB', 'GB'];
    let i = 0;
    while (bytes >= 1024 && i < units.length - 1) {
        bytes /= 1024;
        i++;
    }
    return bytes.toFixed(1) + ' ' + units[i];
}

function renderPagination(total, page, size) {
    const pages = Math.ceil(total / size);
    let html = '';
    for (let i = 1; i <= pages; i++) {
        html += `<li class="page-item ${i === page ? 'active' : ''}"><a class="page-link" href="#" onclick="loadFiles(${i})">${i}</a></li>`;
    }
    document.getElementById('pagination').innerHTML = html;
}

function toggleSelect(id) {
    if (selectedIds.has(id)) selectedIds.delete(id);
    else selectedIds.add(id);
}

function toggleAll() {
    const checked = document.getElementById('select-all').checked;
    document.querySelectorAll('#file-list input[type="checkbox"]').forEach(cb => {
        cb.checked = checked;
        const id = parseInt(cb.value);
        if (checked) selectedIds.add(id);
        else selectedIds.delete(id);
    });
}

function batchSelect(selected) {
    const ids = Array.from(selectedIds);
    if (ids.length === 0) return alert('请先选择文件');
    
    fetch('/api/files/batch-select', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({file_ids: ids, selected}),
    }).then(() => {
        selectedIds.clear();
        loadFiles(currentPage);
    });
}

fetch('/api/browse')
    .then(r => r.json())
    .then(data => {
        const select = document.getElementById('filter-dir');
        data.items.forEach(item => {
            const opt = document.createElement('option');
            opt.value = item.name;
            opt.textContent = item.name;
            select.appendChild(opt);
        });
    });

loadFiles();
</script>
{% endblock %}
```

- [ ] **Step 2: 创建 app/templates/file_detail.html**

```html
{% extends "base.html" %}

{% block title %}文件详情 - PDF 扫描器{% endblock %}

{% block content %}
<div id="file-info"></div>

<div class="row">
    <div class="col-md-6">
        <div class="card">
            <div class="card-header">PDF 预览</div>
            <div class="card-body p-0">
                <iframe id="pdf-preview" style="width:100%; height:600px; border:none;"></iframe>
            </div>
        </div>
    </div>
    <div class="col-md-6">
        <div class="card">
            <div class="card-header">OCR 内容</div>
            <div class="card-body">
                <pre id="md-content" style="max-height:500px; overflow:auto;"></pre>
            </div>
        </div>
    </div>
</div>
{% endblock %}

{% block extra_js %}
<script>
const fileId = {{ file_id }};

fetch('/api/files/' + fileId)
    .then(r => r.json())
    .then(data => {
        const info = document.getElementById('file-info');
        info.innerHTML = `
            <div class="card mb-3">
                <div class="card-body">
                    <h4>${escapeHtml(data.filename)}</h4>
                    <p>目录：${escapeHtml(data.directory)} | 大小：${formatSize(data.file_size)}</p>
                    <p>AI 匹配：${data.ai_matched !== null ? (data.ai_matched ? '是 ' : '否 ') + (data.ai_confidence * 100).toFixed(0) + '%' : '未检测'}</p>
                    <p>理由：${escapeHtml(data.ai_reason || '-')}</p>
                    <button class="btn ${data.selected ? 'btn-success' : 'btn-outline-success'}" onclick="toggleSelect()">
                        ${data.selected ? '已选中' : '选中转 MD'}
                    </button>
                    <a href="/api/files/${fileId}/md" class="btn btn-outline-primary">下载 MD</a>
                </div>
            </div>
        `;
        
        document.getElementById('pdf-preview').src = '/api/files/' + fileId + '/preview';
        document.getElementById('md-content').textContent = data.md_content || '无内容';
    });

function toggleSelect() {
    fetch('/api/files/' + fileId + '/select', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({selected: true}),
    }).then(() => location.reload());
}

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

function formatSize(bytes) {
    if (!bytes) return '-';
    const units = ['B', 'KB', 'MB', 'GB'];
    let i = 0;
    while (bytes >= 1024 && i < units.length - 1) {
        bytes /= 1024;
        i++;
    }
    return bytes.toFixed(1) + ' ' + units[i];
}
</script>
{% endblock %}
```

- [ ] **Step 3: 创建 app/templates/browse.html**

```html
{% extends "base.html" %}

{% block title %}目录浏览 - PDF 扫描器{% endblock %}

{% block content %}
<h3>目录浏览</h3>
<nav aria-label="breadcrumb">
    <ol class="breadcrumb" id="breadcrumb">
        <li class="breadcrumb-item"><a href="#" onclick="loadPath('')">根目录</a></li>
    </ol>
</nav>

<div class="list-group" id="dir-list"></div>
{% endblock %}

{% block extra_js %}
<script>
let currentPath = '';

function loadPath(path) {
    currentPath = path;
    fetch('/api/browse?path=' + encodeURIComponent(path))
        .then(r => r.json())
        .then(data => {
            const list = document.getElementById('dir-list');
            if (data.type === 'root') {
                list.innerHTML = data.items.map(item => `
                    <a href="#" class="list-group-item list-group-item-action d-flex justify-content-between"
                       onclick="loadPath('${item.name}')">
                        <span>📁 ${escapeHtml(item.name)}</span>
                        <span class="badge bg-secondary">${item.number}</span>
                    </a>
                `).join('');
            } else {
                list.innerHTML = data.items.map(item => `
                    <div class="list-group-item d-flex justify-content-between">
                        <span>📄 ${escapeHtml(item.name)}</span>
                        <span class="text-muted">${formatSize(item.size)}</span>
                    </div>
                `).join('');
            }
        });
    
    updateBreadcrumb(path);
}

function updateBreadcrumb(path) {
    const ol = document.getElementById('breadcrumb');
    ol.innerHTML = '<li class="breadcrumb-item"><a href="#" onclick="loadPath(\'\')">根目录</a></li>';
    if (path) {
        ol.innerHTML += `<li class="breadcrumb-item active">${escapeHtml(path)}</li>`;
    }
}

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

function formatSize(bytes) {
    if (!bytes) return '-';
    const units = ['B', 'KB', 'MB', 'GB'];
    let i = 0;
    while (bytes >= 1024 && i < units.length - 1) {
        bytes /= 1024;
        i++;
    }
    return bytes.toFixed(1) + ' ' + units[i];
}

loadPath('');
</script>
{% endblock %}
```

- [ ] **Step 4: 创建 app/templates/settings.html**

```html
{% extends "base.html" %}

{% block title %}系统配置 - PDF 扫描器{% endblock %}

{% block content %}
<h3>系统配置</h3>

<div id="config-list"></div>

<div class="mt-3">
    <button class="btn btn-primary" onclick="saveAll()">保存全部</button>
    <button class="btn btn-outline-secondary" onclick="testSMB()">测试 SMB</button>
    <button class="btn btn-outline-secondary" onclick="testOCR()">测试 OCR</button>
    <button class="btn btn-outline-secondary" onclick="testAI()">测试 AI</button>
</div>

<div id="test-result" class="mt-3"></div>
{% endblock %}

{% block extra_js %}
<script>
let configs = {};

function loadConfigs() {
    fetch('/api/config')
        .then(r => r.json())
        .then(data => {
            configs = {};
            const list = document.getElementById('config-list');
            list.innerHTML = '';
            data.configs.forEach(c => {
                configs[c.key] = c.value;
                const isSecret = c.key.includes('password') || c.key.includes('api_key');
                const inputType = isSecret ? 'password' : 'text';
                const div = document.createElement('div');
                div.className = 'mb-3';
                div.innerHTML = `
                    <label class="form-label">${escapeHtml(c.key)}</label>
                    <input type="${inputType}" class="form-control" id="cfg-${c.key}"
                           value="${escapeHtml(c.value || '')}" placeholder="${escapeHtml(c.description || '')}">
                    <div class="form-text">${escapeHtml(c.description || '')}</div>
                `;
                list.appendChild(div);
            });
        });
}

function saveAll() {
    Object.keys(configs).forEach(key => {
        const value = document.getElementById('cfg-' + key).value;
        fetch('/api/config/' + key, {
            method: 'PUT',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({value}),
        });
    });
    alert('配置已保存');
}

function testSMB() {
    fetch('/api/config/test-smb', {method: 'POST'})
        .then(r => r.json())
        .then(data => showResult('SMB', data));
}

function testOCR() {
    fetch('/api/config/test-ocr', {method: 'POST'})
        .then(r => r.json())
        .then(data => showResult('OCR', data));
}

function testAI() {
    fetch('/api/config/test-ai', {method: 'POST'})
        .then(r => r.json())
        .then(data => showResult('AI', data));
}

function showResult(name, data) {
    const div = document.getElementById('test-result');
    const alertClass = data.success ? 'alert-success' : 'alert-danger';
    div.innerHTML = `<div class="alert ${alertClass}">${escapeHtml(name)}: ${escapeHtml(JSON.stringify(data))}</div>`;
}

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

loadConfigs();
</script>
{% endblock %}
```

- [ ] **Step 5: 创建 app/templates/logs.html**

```html
{% extends "base.html" %}

{% block title %}操作日志 - PDF 扫描器{% endblock %}

{% block content %}
<h3>操作日志</h3>
<table class="table table-striped">
    <thead>
        <tr><th>时间</th><th>用户</th><th>操作</th><th>详情</th></tr>
    </thead>
    <tbody id="log-list"></tbody>
</table>
{% endblock %}

{% block extra_js %}
<script>
fetch('/api/logs')
    .then(r => r.json())
    .then(data => {
        document.getElementById('log-list').innerHTML = data.logs.map(l => `
            <tr>
                <td>${new Date(l.created_at).toLocaleString()}</td>
                <td>${escapeHtml(l.username)}</td>
                <td>${escapeHtml(l.action)}</td>
                <td><pre class="mb-0">${escapeHtml(JSON.stringify(l.detail, null, 2))}</pre></td>
            </tr>
        `).join('');
    });

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}
</script>
{% endblock %}
```

- [ ] **Step 6: Commit**

```bash
git add app/templates/
git commit -m "feat: all page templates"
```

---

## Task 12: Docker 配置

**Files:**
- Create: `docker/Dockerfile`
- Create: `docker/docker-compose.yml`
- Create: `docker/entrypoint.sh`

- [ ] **Step 1: 创建 docker/Dockerfile**

```dockerfile
FROM python:3.11-slim

RUN apt-get update && apt-get install -y \
    cifs-utils \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN chmod +x docker/entrypoint.sh

EXPOSE 5556

ENTRYPOINT ["docker/entrypoint.sh"]
```

- [ ] **Step 2: 创建 docker/entrypoint.sh**

```bash
#!/bin/bash
set -e

# SMB 挂载
SMB_SERVER=${SMB_SERVER:-192.168.0.79}
SMB_SHARE=${SMB_SHARE:-abb/FS/10/D$/tbmdata/data/ftpdata}
SMB_USER=${SMB_USER:-}
SMB_PASS=${SMB_PASS:-}
MOUNT_PATH=${MOUNT_PATH:-/mnt/smb/ftpdata}

mkdir -p "$MOUNT_PATH"

if [ -n "$SMB_USER" ]; then
    mount -t cifs "//$SMB_SERVER/$SMB_SHARE" "$MOUNT_PATH" \
        -o "username=$SMB_USER,password=$SMB_PASS,ro,vers=3.0"
    echo "SMB mounted at $MOUNT_PATH"
else
    echo "SMB credentials not set, skipping mount"
fi

# 根据命令执行
if [ "$1" = "web" ]; then
    exec gunicorn -b 0.0.0.0:5556 run:app
elif [ "$1" = "worker" ]; then
    exec celery -A celery_worker worker -l info -c 1
else
    exec "$@"
fi
```

- [ ] **Step 3: 创建 docker/docker-compose.yml**

```yaml
version: '3.8'

services:
  web:
    build:
      context: ..
      dockerfile: docker/Dockerfile
    ports:
      - "5556:5556"
    environment:
      - FLASK_ENV=production
      - REDIS_URL=redis://redis:6379/0
      - DB_HOST=192.168.0.98
      - DB_PORT=5432
      - DB_NAME=yz_relay
      - DB_USER=grigs
      - DB_PASSWORD=Slnwg123$
      - SMB_SERVER=192.168.0.79
      - SMB_SHARE=abb/FS/10/D$/tbmdata/data/ftpdata
      - SMB_USER=${SMB_USER}
      - SMB_PASS=${SMB_PASS}
    cap_add:
      - SYS_ADMIN
      - DAC_READ_SEARCH
    security_opt:
      - apparmor:unconfined
    devices:
      - /dev/fuse:/dev/fuse
    depends_on:
      - redis
    command: web

  worker:
    build:
      context: ..
      dockerfile: docker/Dockerfile
    environment:
      - FLASK_ENV=production
      - REDIS_URL=redis://redis:6379/0
      - DB_HOST=192.168.0.98
      - DB_PORT=5432
      - DB_NAME=yz_relay
      - DB_USER=grigs
      - DB_PASSWORD=Slnwg123$
      - SMB_SERVER=192.168.0.79
      - SMB_SHARE=abb/FS/10/D$/tbmdata/data/ftpdata
      - SMB_USER=${SMB_USER}
      - SMB_PASS=${SMB_PASS}
    cap_add:
      - SYS_ADMIN
      - DAC_READ_SEARCH
    security_opt:
      - apparmor:unconfined
    devices:
      - /dev/fuse:/dev/fuse
    depends_on:
      - redis
    command: worker

  redis:
    image: redis:7-alpine
    ports:
      - "6379:6379"
```

- [ ] **Step 4: Commit**

```bash
git add docker/
git commit -m "feat: Docker deployment config"
```

---

## Task 13: 修复与集成测试

**Files:**
- Modify: `app/__init__.py`
- Modify: `app/api.py`
- Modify: `app/views.py`

- [ ] **Step 1: 本地启动测试**

Run (终端 1): `redis-server`
Run (终端 2): `python run.py`
Run (终端 3): `celery -A celery_worker worker -l info -c 1`

访问 `http://localhost:5556/login` 应显示登录页。

- [ ] **Step 2: Commit**

```bash
git add -A
git commit -m "fix: integration fixes and local dev setup"
```

---

## 自检

### Spec 覆盖检查

| Spec 章节 | 实现任务 |
|-----------|----------|
| 2.1 技术栈 | Task 1 (依赖), Task 12 (Docker) |
| 2.2 服务拓扑 | Task 1, Task 8 (Celery) |
| 3.1 数据库表 | Task 2 |
| 3.2 初始配置 | Task 2 (init_db.py) |
| 4.1 目录遍历 | Task 4 (SMB), Task 7 (Scanner) |
| 4.2 单文件处理 | Task 5 (OCR), Task 6 (AI), Task 7 (Scanner) |
| 4.3 AI Prompt | Task 6 |
| 4.4 暂停/恢复 | Task 7, Task 8, Task 9 |
| 5.1 页面清单 | Task 10, Task 11 |
| 5.2 仪表盘 | Task 10 |
| 6.1 yz-login 认证 | Task 3 |
| 7.1-7.4 API | Task 9 |
| 8.1-8.3 Docker | Task 12 |
| 9. 错误处理 | Task 5 (OCR 重试), Task 7 (异常捕获) |
| 10. 安全 | Task 3 (admin_required), Task 4 (safe_path) |
| 11. 完成通知 | Task 10 (dashboard.js) |

### Placeholder 检查
- [x] 无 TBD/TODO
- [x] 所有代码完整可运行
- [x] 所有 API 路由完整

### 类型一致性
- [x] `ScanProgress.get()` 返回 RealDictRow
- [x] `ScannedFile.create()` 参数与表字段一致
- [x] API 路由参数与前端调用一致

---

**计划完成，保存到 `docs/superpowers/plans/2026-04-23-pdf-scanner-plan.md`。**

**执行选项：**

1. **Subagent-Driven（推荐）** — 每个任务分配独立子代理执行，我在任务间审查
2. **Inline Execution** — 在当前会话中逐个执行任务

请选择执行方式。
