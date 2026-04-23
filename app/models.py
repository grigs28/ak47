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
        # 处理 CURRENT_TIMESTAMP 特殊值（openGauss 不支持作为参数）
        fields = []
        values = []
        for k, v in kwargs.items():
            if v == 'CURRENT_TIMESTAMP' or v == 'NOW()':
                fields.append(f"{k} = CURRENT_TIMESTAMP")
            else:
                fields.append(f"{k} = %s")
                values.append(v)
        fields_str = ', '.join(fields)
        execute(f"UPDATE {cls.TABLE} SET {fields_str}, updated_at = CURRENT_TIMESTAMP WHERE id = 1", values)

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
    def list(cls, directory=None, selected=None, ai_matched=None, design_number=None, page=1, size=20):
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
        if design_number:
            where.append("设计编号 = %s")
            params.append(design_number)

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
        # openGauss 不支持 ON CONFLICT，使用先更新后插入
        from app.db import get_conn
        conn = get_conn()
        with conn.cursor() as cur:
            cur.execute(f"UPDATE {cls.TABLE} SET value = %s, updated_at = CURRENT_TIMESTAMP WHERE key = %s",
                        (value, key))
            if cur.rowcount == 0:
                cur.execute(f"INSERT INTO {cls.TABLE} (key, value) VALUES (%s, %s)", (key, value))
        conn.commit()

    @classmethod
    def all(cls):
        return query(f"SELECT * FROM {cls.TABLE} ORDER BY key", fetchall=True)

class OperationLog:
    TABLE = 'operation_logs'

    @classmethod
    def create(cls, user_id, username, action, detail=None):
        import json
        detail_json = json.dumps(detail) if detail else None
        execute(f"INSERT INTO {cls.TABLE} (user_id, username, action, detail) VALUES (%s, %s, %s, %s::jsonb)",
                (user_id, username, action, detail_json))

    @classmethod
    def list(cls, page=1, size=50):
        offset = (page - 1) * size
        return query(f"SELECT * FROM {cls.TABLE} ORDER BY created_at DESC LIMIT %s OFFSET %s",
                     (size, offset), fetchall=True)
