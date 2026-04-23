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
        INSERT INTO system_config (key, value, description) VALUES ('smb_server', '192.168.0.79', 'SMB 服务器地址');
    END IF;
    IF NOT EXISTS (SELECT 1 FROM system_config WHERE key = 'smb_share') THEN
        INSERT INTO system_config (key, value, description) VALUES ('smb_share', 'abb/FS/10/D$/tbmdata/data/ftpdata', 'SMB 共享路径');
    END IF;
    IF NOT EXISTS (SELECT 1 FROM system_config WHERE key = 'smb_username') THEN
        INSERT INTO system_config (key, value, description) VALUES ('smb_username', '', 'SMB 用户名');
    END IF;
    IF NOT EXISTS (SELECT 1 FROM system_config WHERE key = 'smb_password') THEN
        INSERT INTO system_config (key, value, description) VALUES ('smb_password', '', 'SMB 密码');
    END IF;
    IF NOT EXISTS (SELECT 1 FROM system_config WHERE key = 'smb_mount_path') THEN
        INSERT INTO system_config (key, value, description) VALUES ('smb_mount_path', '/mnt/smb/ftpdata', 'SMB 容器内挂载点');
    END IF;
    IF NOT EXISTS (SELECT 1 FROM system_config WHERE key = 'paddleocr_base_url') THEN
        INSERT INTO system_config (key, value, description) VALUES ('paddleocr_base_url', 'http://192.168.0.19:5553', 'PaddleOCR-ui 地址');
    END IF;
    IF NOT EXISTS (SELECT 1 FROM system_config WHERE key = 'paddleocr_api_key') THEN
        INSERT INTO system_config (key, value, description) VALUES ('paddleocr_api_key', 'ak_e10b412d5cd68eeef303c3f561405dfb07d7e122123df8f97d0ecb30e5624d', 'PaddleOCR API Key');
    END IF;
    IF NOT EXISTS (SELECT 1 FROM system_config WHERE key = 'qwen_base_url') THEN
        INSERT INTO system_config (key, value, description) VALUES ('qwen_base_url', 'http://192.168.0.18:5566/v1', 'qwen-3 API 地址');
    END IF;
    IF NOT EXISTS (SELECT 1 FROM system_config WHERE key = 'qwen_api_key') THEN
        INSERT INTO system_config (key, value, description) VALUES ('qwen_api_key', '', 'qwen-3 API Key');
    END IF;
    IF NOT EXISTS (SELECT 1 FROM system_config WHERE key = 'qwen_model') THEN
        INSERT INTO system_config (key, value, description) VALUES ('qwen_model', 'qwen3', 'qwen-3 模型名');
    END IF;
    IF NOT EXISTS (SELECT 1 FROM system_config WHERE key = 'yz_login_url') THEN
        INSERT INTO system_config (key, value, description) VALUES ('yz_login_url', 'http://192.168.0.19:5555', 'yz-login 地址');
    END IF;
    IF NOT EXISTS (SELECT 1 FROM system_config WHERE key = 'scan_concurrency') THEN
        INSERT INTO system_config (key, value, description) VALUES ('scan_concurrency', '1', '并发扫描数');
    END IF;
    IF NOT EXISTS (SELECT 1 FROM system_config WHERE key = 'ai_enabled') THEN
        INSERT INTO system_config (key, value, description) VALUES ('ai_enabled', 'true', '是否启用 AI 辅助匹配');
    END IF;
    IF NOT EXISTS (SELECT 1 FROM system_config WHERE key = 'gbt_standard') THEN
        INSERT INTO system_config (key, value, description) VALUES ('gbt_standard', 'GBT 50378-2019(2024年版)', '绿色建筑标准名称');
    END IF;
END $$;
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
