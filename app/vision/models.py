import threading
from app.db import query, execute


# === 全局共享设计编号缓存（内存 + 数据库持久化） ===
class DesignCacheMemory:
    """线程安全的设计编号内存缓存，扫描前从数据库加载"""

    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._cache = set()
                    cls._instance._init_lock = threading.Lock()
                    cls._instance._initialized = False
        return cls._instance

    def load_from_db(self):
        """从数据库加载所有已标记的设计编号到内存"""
        with self._init_lock:
            if self._initialized:
                return
            rows = query("SELECT 设计编号 FROM design_cache WHERE has_instruction = TRUE", fetchall=True)
            self._cache = set(row['设计编号'] for row in (rows or []) if row.get('设计编号'))
            self._initialized = True
            print(f"[DesignCacheMemory] 加载 {len(self._cache)} 个设计编号到内存缓存")

    def should_skip(self, design_number):
        """内存中判断是否应跳过"""
        if not design_number or design_number == 'unknown':
            return False
        return design_number in self._cache

    def mark(self, design_number):
        """标记设计编号（内存 + 数据库）"""
        if not design_number or design_number == 'unknown':
            return
        with self._init_lock:
            self._cache.add(design_number)
        # 同步写入数据库
        DesignCache.create_or_update(design_number, has_instruction=True)

    def reset(self):
        """重置缓存（用于测试）"""
        with self._init_lock:
            self._cache.clear()
            self._initialized = False


# 全局单例
design_cache_memory = DesignCacheMemory()


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
    def get_by_path(cls, file_path):
        return query(f"SELECT * FROM {cls.TABLE} WHERE file_path = %s", (file_path,), fetchone=True)

    @classmethod
    def update(cls, file_id, **kwargs):
        if not kwargs:
            return
        fields = ', '.join(f"{k} = %s" for k in kwargs)
        values = list(kwargs.values()) + [file_id]
        execute(f"UPDATE {cls.TABLE} SET {fields}, updated_at = CURRENT_TIMESTAMP WHERE id = %s", values)

    @classmethod
    def delete(cls, file_id):
        execute(f"DELETE FROM {cls.TABLE} WHERE id = %s", (file_id,))

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
                # 处理 CURRENT_TIMESTAMP 特殊值
                fields = []
                values = []
                for k, v in kwargs.items():
                    if v == 'CURRENT_TIMESTAMP':
                        fields.append(f"{k} = CURRENT_TIMESTAMP")
                    else:
                        fields.append(f"{k} = %s")
                        values.append(v)
                fields_str = ', '.join(fields)
                values.append(design_number)
                cur.execute(f"UPDATE {cls.TABLE} SET {fields_str}, updated_at = CURRENT_TIMESTAMP WHERE 设计编号 = %s", values)
            if cur.rowcount == 0:
                columns = ['设计编号'] + list(kwargs.keys())
                placeholders = ', '.join('%s' for _ in columns)
                values = [design_number] + list(kwargs.values())
                cur.execute(f"INSERT INTO {cls.TABLE} ({', '.join(columns)}) VALUES ({placeholders})", values)
        conn.commit()

    @classmethod
    def should_skip(cls, design_number):
        """判断该设计编号是否应跳过（先查内存缓存，再查数据库）"""
        # 优先查内存缓存（线程安全，快速）
        if design_cache_memory.should_skip(design_number):
            return True
        # 内存未命中，查数据库
        row = cls.get(design_number)
        if row and row.get('has_instruction'):
            # 同步到内存缓存
            design_cache_memory.mark(design_number)
            return True
        return False

    @classmethod
    def list(cls, page=1, size=20):
        offset = (page - 1) * size
        rows = query(f"SELECT * FROM {cls.TABLE} ORDER BY updated_at DESC LIMIT %s OFFSET %s",
                     (size, offset), fetchall=True)
        total = query(f"SELECT COUNT(*) as cnt FROM {cls.TABLE}", fetchone=True)
        return rows, total['cnt'] if total else 0


class ScannedDirectory:
    TABLE = 'scanned_directories'

    @classmethod
    def get(cls, directory):
        return query(f"SELECT * FROM {cls.TABLE} WHERE directory = %s", (directory,), fetchone=True)

    @classmethod
    def create_or_update(cls, directory, **kwargs):
        from app.db import get_conn
        conn = get_conn()
        with conn.cursor() as cur:
            if kwargs:
                # 处理 CURRENT_TIMESTAMP 特殊值
                fields = []
                values = []
                for k, v in kwargs.items():
                    if v == 'CURRENT_TIMESTAMP':
                        fields.append(f"{k} = CURRENT_TIMESTAMP")
                    else:
                        fields.append(f"{k} = %s")
                        values.append(v)
                fields_str = ', '.join(fields)
                values.append(directory)
                cur.execute(f"UPDATE {cls.TABLE} SET {fields_str}, updated_at = CURRENT_TIMESTAMP WHERE directory = %s", values)
            if cur.rowcount == 0:
                columns = ['directory'] + list(kwargs.keys())
                placeholders = ', '.join('%s' for _ in columns)
                values = [directory] + list(kwargs.values())
                cur.execute(f"INSERT INTO {cls.TABLE} ({', '.join(columns)}) VALUES ({placeholders})", values)
        conn.commit()

    @classmethod
    def list(cls, status=None, page=1, size=100):
        where = []
        params = []
        if status:
            where.append("status = %s")
            params.append(status)
        where_clause = f"WHERE {' AND '.join(where)}" if where else ""
        offset = (page - 1) * size
        rows = query(f"SELECT * FROM {cls.TABLE} {where_clause} ORDER BY updated_at DESC LIMIT %s OFFSET %s",
                     (*params, size, offset), fetchall=True)
        total = query(f"SELECT COUNT(*) as cnt FROM {cls.TABLE} {where_clause}", params, fetchone=True)
        return rows, total['cnt'] if total else 0

    @classmethod
    def mark_completed(cls, directory):
        # 使用 NOW() 而不是字符串 'CURRENT_TIMESTAMP'
        from app.db import get_conn
        conn = get_conn()
        with conn.cursor() as cur:
            cur.execute(f"UPDATE {cls.TABLE} SET status = 'completed', completed_at = NOW(), updated_at = NOW() WHERE directory = %s", (directory,))
            if cur.rowcount == 0:
                cur.execute(f"INSERT INTO {cls.TABLE} (directory, status, completed_at, updated_at) VALUES (%s, 'completed', NOW(), NOW())", (directory,))
        conn.commit()

    @classmethod
    def is_completed(cls, directory):
        row = cls.get(directory)
        return row is not None and row.get('status') == 'completed'
