from app.db import query, execute


class TempFile:
    TABLE = 'temp_files'

    @classmethod
    def create(cls, **kwargs):
        columns = ', '.join(kwargs.keys())
        placeholders = ', '.join('%s' for _ in kwargs)
        execute(f"INSERT INTO {cls.TABLE} ({columns}) VALUES ({placeholders})", list(kwargs.values()))
        # 返回刚插入的行
        return query(f"SELECT * FROM {cls.TABLE} WHERE file_path = %s ORDER BY id DESC LIMIT 1",
                     (kwargs.get('file_path'),), fetchone=True)

    @classmethod
    def get(cls, file_id):
        return query(f"SELECT * FROM {cls.TABLE} WHERE id = %s", (file_id,), fetchone=True)

    @classmethod
    def update(cls, file_id, **kwargs):
        if not kwargs:
            return
        fields = ', '.join(f"{k} = %s" for k in kwargs)
        values = list(kwargs.values()) + [file_id]
        execute(f"UPDATE {cls.TABLE} SET {fields}, updated_at = CURRENT_TIMESTAMP WHERE id = %s", values)

    @classmethod
    def list(cls, design_number=None, status=None, page=1, size=20):
        where = []
        params = []
        if design_number:
            where.append("设计编号 = %s")
            params.append(design_number)
        if status:
            where.append("status = %s")
            params.append(status)
        where_clause = f"WHERE {' AND '.join(where)}" if where else ""
        offset = (page - 1) * size
        rows = query(f"SELECT * FROM {cls.TABLE} {where_clause} ORDER BY created_at DESC LIMIT %s OFFSET %s",
                     (*params, size, offset), fetchall=True)
        total = query(f"SELECT COUNT(*) as cnt FROM {cls.TABLE} {where_clause}", params, fetchone=True)
        return rows, total['cnt'] if total else 0


class DesignCache:
    TABLE = 'design_cache'

    @classmethod
    def get(cls, design_number):
        return query(f"SELECT * FROM {cls.TABLE} WHERE 设计编号 = %s", (design_number,), fetchone=True)

    @classmethod
    def create_or_update(cls, design_number, **kwargs):
        # openGauss 不支持 ON CONFLICT，先更新后插入
        from app.db import get_conn
        conn = get_conn()
        with conn.cursor() as cur:
            if kwargs:
                fields = ', '.join(f"{k} = %s" for k in kwargs)
                values = list(kwargs.values()) + [design_number]
                cur.execute(f"UPDATE {cls.TABLE} SET {fields}, updated_at = CURRENT_TIMESTAMP WHERE 设计编号 = %s", values)
            if cur.rowcount == 0:
                columns = ['设计编号'] + list(kwargs.keys())
                placeholders = ', '.join('%s' for _ in columns)
                values = [design_number] + list(kwargs.values())
                cur.execute(f"INSERT INTO {cls.TABLE} ({', '.join(columns)}) VALUES ({placeholders})", values)
        conn.commit()

    @classmethod
    def list(cls, page=1, size=20):
        offset = (page - 1) * size
        rows = query(f"SELECT * FROM {cls.TABLE} ORDER BY updated_at DESC LIMIT %s OFFSET %s",
                     (size, offset), fetchall=True)
        total = query(f"SELECT COUNT(*) as cnt FROM {cls.TABLE}", fetchone=True)
        return rows, total['cnt'] if total else 0
