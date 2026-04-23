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

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM scan_progress WHERE id = 1) THEN
        INSERT INTO scan_progress (id, status) VALUES (1, 'idle');
    END IF;
END $$;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM system_config WHERE key = 'smb_server') THEN
        INSERT INTO system_config (key, value, description) VALUES ('smb_server', '192.168.0.79', '服务器地址');
    END IF;
    IF NOT EXISTS (SELECT 1 FROM system_config WHERE key = 'smb_share') THEN
        INSERT INTO system_config (key, value, description) VALUES ('smb_share', 'abb/FS/10/D$/tbmdata/data/ftpdata', '共享路径');
    END IF;
    IF NOT EXISTS (SELECT 1 FROM system_config WHERE key = 'smb_username') THEN
        INSERT INTO system_config (key, value, description) VALUES ('smb_username', '', '用户名');
    END IF;
    IF NOT EXISTS (SELECT 1 FROM system_config WHERE key = 'smb_password') THEN
        INSERT INTO system_config (key, value, description) VALUES ('smb_password', '', '密码');
    END IF;
    IF NOT EXISTS (SELECT 1 FROM system_config WHERE key = 'smb_domain') THEN
        INSERT INTO system_config (key, value, description) VALUES ('smb_domain', '', '域（可选）');
    END IF;
    IF NOT EXISTS (SELECT 1 FROM system_config WHERE key = 'smb_mount_path') THEN
        INSERT INTO system_config (key, value, description) VALUES ('smb_mount_path', '', '容器内挂载点（自动生成后锁定）');
    END IF;
    IF NOT EXISTS (SELECT 1 FROM system_config WHERE key = 'paddleocr_base_url') THEN
        INSERT INTO system_config (key, value, description) VALUES ('paddleocr_base_url', 'http://192.168.0.19:5553', 'API 地址');
    END IF;
    IF NOT EXISTS (SELECT 1 FROM system_config WHERE key = 'paddleocr_api_key') THEN
        INSERT INTO system_config (key, value, description) VALUES ('paddleocr_api_key', '', 'API Key');
    END IF;
    IF NOT EXISTS (SELECT 1 FROM system_config WHERE key = 'qwen_base_url') THEN
        INSERT INTO system_config (key, value, description) VALUES ('qwen_base_url', 'http://192.168.0.18:5566/v1', 'API 地址');
    END IF;
    IF NOT EXISTS (SELECT 1 FROM system_config WHERE key = 'qwen_api_key') THEN
        INSERT INTO system_config (key, value, description) VALUES ('qwen_api_key', '', 'API Key');
    END IF;
    IF NOT EXISTS (SELECT 1 FROM system_config WHERE key = 'qwen_model') THEN
        INSERT INTO system_config (key, value, description) VALUES ('qwen_model', 'qwen-3', '模型名');
    END IF;
    IF NOT EXISTS (SELECT 1 FROM system_config WHERE key = 'yz_login_url') THEN
        INSERT INTO system_config (key, value, description) VALUES ('yz_login_url', 'http://192.168.0.18:5551', '登录服务地址');
    END IF;
    IF NOT EXISTS (SELECT 1 FROM system_config WHERE key = 'scan_concurrency') THEN
        INSERT INTO system_config (key, value, description) VALUES ('scan_concurrency', '1', '并发数');
    END IF;
    IF NOT EXISTS (SELECT 1 FROM system_config WHERE key = 'ai_enabled') THEN
        INSERT INTO system_config (key, value, description) VALUES ('ai_enabled', 'true', '启用 AI 辅助匹配');
    END IF;
    IF NOT EXISTS (SELECT 1 FROM system_config WHERE key = 'gbt_standard') THEN
        INSERT INTO system_config (key, value, description) VALUES ('gbt_standard', 'GBT 50378-2019(2024年版)', '标准名称');
    END IF;
    IF NOT EXISTS (SELECT 1 FROM system_config WHERE key = 'db_host') THEN
        INSERT INTO system_config (key, value, description) VALUES ('db_host', '192.168.0.98', '数据库地址');
    END IF;
    IF NOT EXISTS (SELECT 1 FROM system_config WHERE key = 'db_port') THEN
        INSERT INTO system_config (key, value, description) VALUES ('db_port', '5432', '数据库端口');
    END IF;
    IF NOT EXISTS (SELECT 1 FROM system_config WHERE key = 'db_name') THEN
        INSERT INTO system_config (key, value, description) VALUES ('db_name', 'yz_relay', '数据库名');
    END IF;
    IF NOT EXISTS (SELECT 1 FROM system_config WHERE key = 'db_user') THEN
        INSERT INTO system_config (key, value, description) VALUES ('db_user', 'grigs', '数据库用户名');
    END IF;
    IF NOT EXISTS (SELECT 1 FROM system_config WHERE key = 'db_password') THEN
        INSERT INTO system_config (key, value, description) VALUES ('db_password', '', '数据库密码');
    END IF;
END $$;

CREATE TABLE IF NOT EXISTS temp_files (
    id              SERIAL PRIMARY KEY,
    file_path       VARCHAR(1000) NOT NULL,
    directory       VARCHAR(500) NOT NULL,
    filename        VARCHAR(500) NOT NULL,
    file_size       BIGINT,

    建设单位        VARCHAR(500),
    工程名称        VARCHAR(1000),
    设计编号        VARCHAR(100) NOT NULL,
    图名            VARCHAR(500),
    图号            VARCHAR(50),
    图别            VARCHAR(50),

    is_instruction  BOOLEAN DEFAULT NULL,
    status          VARCHAR(20) DEFAULT 'pending',

    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (file_path)
);

CREATE INDEX IF NOT EXISTS idx_temp_files_design_number ON temp_files(设计编号);
CREATE INDEX IF NOT EXISTS idx_temp_files_status ON temp_files(status);

CREATE TABLE IF NOT EXISTS design_cache (
    设计编号        VARCHAR(100) PRIMARY KEY,
    建设单位        VARCHAR(500),
    工程名称        VARCHAR(1000),
    has_instruction BOOLEAN DEFAULT FALSE,
    instruction_count INTEGER DEFAULT 0,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

ALTER TABLE scanned_files ADD COLUMN IF NOT EXISTS 建设单位 VARCHAR(500);
ALTER TABLE scanned_files ADD COLUMN IF NOT EXISTS 工程名称 VARCHAR(1000);
ALTER TABLE scanned_files ADD COLUMN IF NOT EXISTS 设计编号 VARCHAR(100);
ALTER TABLE scanned_files ADD COLUMN IF NOT EXISTS 图名 VARCHAR(500);
ALTER TABLE scanned_files ADD COLUMN IF NOT EXISTS 图号 VARCHAR(50);
ALTER TABLE scanned_files ADD COLUMN IF NOT EXISTS 图别 VARCHAR(50);
ALTER TABLE scanned_files ADD COLUMN IF NOT EXISTS json_result JSONB;
ALTER TABLE scanned_files ADD COLUMN IF NOT EXISTS is_instruction BOOLEAN DEFAULT NULL;

CREATE INDEX IF NOT EXISTS idx_scanned_files_design_number ON scanned_files(设计编号);
"""

def init():
    with psycopg2.connect(DSN) as conn:
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute(INIT_SQL)
    print("Database initialized.")

if __name__ == '__main__':
    init()
