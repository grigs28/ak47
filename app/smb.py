import os
import json
import subprocess
import shutil
from datetime import datetime
from app.models import SystemConfig


class SMBManager:
    """支持多个 SMB 共享路径的管理器
    每个共享路径挂载到独立目录，逻辑上合并为一个大目录列表
    """
    _mount_points = {}  # {share_id: mount_path}
    _common_config = None  # server/username/password/domain 缓存

    @classmethod
    def _get_common_config(cls):
        """获取共用的 SMB 配置（服务器、用户名、密码、域）"""
        if cls._common_config is None:
            cls._common_config = {
                'server': SystemConfig.get('smb_server', '192.168.0.79'),
                'username': SystemConfig.get('smb_username', ''),
                'password': SystemConfig.get('smb_password', ''),
                'domain': SystemConfig.get('smb_domain', ''),
            }
        return cls._common_config

    @classmethod
    def get_shares(cls):
        """获取所有配置的共享路径列表，返回 [{id, name, server, share, mount_path, mounted}, ...]"""
        shares_json = SystemConfig.get('smb_shares', '')
        if not shares_json:
            # 兼容旧配置：从 smb_share 生成默认
            old_share = SystemConfig.get('smb_share', 'abb/FS/10/D$/tbmdata/data/ftpdata')
            shares = [{'name': '默认', 'server': '', 'share': old_share}]
        else:
            try:
                shares = json.loads(shares_json)
            except (json.JSONDecodeError, TypeError):
                shares = [{'name': '默认', 'server': '', 'share': shares_json}]

        # 为每个 share 分配 id 和挂载路径
        for i, s in enumerate(shares):
            s['id'] = i
            if 'server' not in s or not s['server']:
                s['server'] = cls._get_common_config()['server']
            mount_path = cls._get_mount_path_for_share(s)
            s['mount_path'] = mount_path
            s['mounted'] = os.path.ismount(mount_path) if mount_path else False
        return shares

    # 挂载基础目录：Docker容器用 /mnt/smb，本地用 ~/mnt/ak47
    _mount_base = None

    @classmethod
    def _get_mount_base(cls):
        if cls._mount_base:
            return cls._mount_base
        # 优先用环境变量
        env_base = os.environ.get('AK47_MOUNT_BASE', '')
        if env_base:
            cls._mount_base = env_base
        elif os.path.exists('/.dockerenv'):
            cls._mount_base = '/mnt/smb'
        else:
            cls._mount_base = os.path.join(os.path.expanduser('~'), 'mnt', 'ak47')
        return cls._mount_base

    @classmethod
    def _get_mount_path_for_share(cls, share):
        """根据 share 配置找到或生成挂载路径"""
        share_path = share.get('share', '')
        share_clean = share_path.lstrip('\\').replace('\\', '/')
        ak47_base = cls._get_mount_base()

        # 从 /proc/mounts 精确匹配挂载点
        try:
            with open('/proc/mounts', 'r') as f:
                for line in f:
                    parts = line.split()
                    if len(parts) >= 2 and parts[2] == 'cifs':
                        mount_url = parts[0]  # 如 //192.168.0.79/abb/FS
                        mount_dir = parts[1]   # 如 /mnt/smb/abb_FS
                        if not mount_dir.startswith(ak47_base):
                            continue
                        url_path = mount_url.split('/', 3)[-1] if '/' in mount_url[2:] else ''
                        if url_path == share_clean:
                            return mount_dir
        except Exception:
            pass

        # 没有已挂载的，生成新路径
        safe_name = share_path.replace('/', '_').replace('\\', '_').replace('$', '').replace(' ', '')
        while '__' in safe_name:
            safe_name = safe_name.replace('__', '_')
        safe_name = safe_name.strip('_')
        return os.path.join(ak47_base, safe_name)

    @classmethod
    def save_shares(cls, shares_list):
        """保存共享路径列表到数据库"""
        # 只保存 name/server/share，不保存 id/mount_path/mounted
        data = [{'name': s.get('name', ''), 'server': s.get('server', ''), 'share': s.get('share', '')} for s in shares_list]
        SystemConfig.set('smb_shares', json.dumps(data, ensure_ascii=False))
        # 清除缓存
        cls._mount_points.clear()

    @classmethod
    def mount_share(cls, share):
        """挂载单个共享路径"""
        cfg = cls._get_common_config()
        server = share.get('server') or cfg['server']
        share_path = share.get('share', '')
        mount_path = cls._get_mount_path_for_share(share)

        # 已挂载
        if os.path.ismount(mount_path):
            cls._mount_points[share.get('id', 0)] = mount_path
            return True

        os.makedirs(mount_path, exist_ok=True)

        share_clean = share_path.lstrip('\\').replace('\\', '/')
        share_url = f"//{server}/{share_clean}"
        opts = f"vers=3.0,sec=ntlmssp,username={cfg['username']},password={cfg['password']},uid={os.getuid()},gid={os.getgid()},file_mode=0777,dir_mode=0777,ro"
        if cfg['domain']:
            opts += f",domain={cfg['domain']}"

        # Docker 容器内直接 mount，本地用 sudo
        if os.path.exists('/.dockerenv'):
            cmd = ['mount', '-t', 'cifs', share_url, mount_path, '-o', opts]
            result = subprocess.run(cmd, capture_output=True, text=True)
        else:
            cmd = ['sudo', '-S', 'mount', '-t', 'cifs', share_url, mount_path, '-o', opts]
            result = subprocess.run(cmd, capture_output=True, text=True, input='Slnwg123$\n')

        if result.returncode != 0:
            raise RuntimeError(f"SMB mount failed: {result.stderr}")

        cls._mount_points[share.get('id', 0)] = mount_path
        return True

    @classmethod
    def mount_all(cls):
        """挂载所有已配置的共享路径"""
        shares = cls.get_shares()
        errors = []
        for s in shares:
            try:
                cls.mount_share(s)
            except Exception as e:
                errors.append(f"{s['name']}: {e}")
        if errors:
            raise RuntimeError("部分挂载失败: " + "; ".join(errors))
        return True

    @classmethod
    def umount_all(cls):
        """卸载所有挂载点"""
        shares = cls.get_shares()
        for s in shares:
            mount_path = s.get('mount_path', '')
            if mount_path and os.path.ismount(mount_path):
                if os.path.exists('/.dockerenv'):
                    subprocess.run(['umount', mount_path], capture_output=True, text=True)
                else:
                    subprocess.run(['sudo', '-S', 'umount', mount_path], capture_output=True, text=True, input='Slnwg123$\n')
        cls._mount_points.clear()

    @classmethod
    def is_any_mounted(cls):
        """是否有任何挂载点已挂载"""
        shares = cls.get_shares()
        return any(s.get('mounted') for s in shares)

    @classmethod
    def _get_active_mount_paths(cls):
        """获取所有已挂载的挂载点路径列表"""
        shares = cls.get_shares()
        return [s['mount_path'] for s in shares if s.get('mounted')]

    @classmethod
    def _find_mount_path_for_dir(cls, directory):
        """根据目录名找到对应的挂载点路径"""
        for mount_path in cls._get_active_mount_paths():
            full = os.path.join(mount_path, directory)
            if os.path.isdir(full):
                return mount_path
        # 兜底：返回第一个挂载点
        paths = cls._get_active_mount_paths()
        return paths[0] if paths else None

    @classmethod
    def _find_mount_path_for_file(cls, relative_path):
        """根据文件相对路径找到对应的挂载点
        relative_path 格式: "目录名/子目录/文件.pdf"
        """
        parts = relative_path.split(os.sep)
        if not parts:
            return None
        top_dir = parts[0]
        return cls._find_mount_path_for_dir(top_dir)

    # === 兼容旧接口 ===

    @classmethod
    def get_mount_path(cls):
        """兼容旧接口：返回第一个挂载点"""
        paths = cls._get_active_mount_paths()
        return paths[0] if paths else cls._get_mount_path_for_share({'share': 'default'})

    @classmethod
    def is_mounted(cls):
        """兼容旧接口"""
        return cls.is_any_mounted()

    @classmethod
    def mount(cls):
        """兼容旧接口：挂载所有"""
        return cls.mount_all()

    @classmethod
    def umount(cls):
        """兼容旧接口：卸载所有"""
        return cls.umount_all()

    @classmethod
    def list_dirs(cls, page=1, size=100):
        """合并所有挂载点的子目录，按修改时间降序排列"""
        all_dirs = []
        for mount_path in cls._get_active_mount_paths():
            try:
                for name in os.listdir(mount_path):
                    full = os.path.join(mount_path, name)
                    if os.path.isdir(full):
                        mtime = os.path.getmtime(full)
                        all_dirs.append((name, mtime))
            except OSError:
                continue

        # 去重（同名目录取最新mtime）
        dir_map = {}
        for name, mtime in all_dirs:
            if name not in dir_map or mtime > dir_map[name]:
                dir_map[name] = mtime

        dirs = list(dir_map.items())
        dirs.sort(key=lambda x: x[1], reverse=True)

        total = len(dirs)
        start = (page - 1) * size
        end = start + size
        return dirs[start:end], total

    @classmethod
    def list_pdfs(cls, directory, recursive=True, max_depth=256):
        """列出指定目录下所有 PDF 文件，自动定位到正确的挂载点"""
        mount_path = cls._find_mount_path_for_dir(directory)
        if not mount_path:
            return []

        dir_path = os.path.join(mount_path, directory)

        real_dir = os.path.realpath(dir_path)
        real_mount = os.path.realpath(mount_path)
        if not real_dir.startswith(real_mount):
            raise ValueError("Invalid directory path")

        pdfs = []
        if recursive:
            base_depth = real_dir.rstrip(os.sep).count(os.sep)
            for root, dirs, files in os.walk(real_dir):
                current_depth = root.rstrip(os.sep).count(os.sep)
                if current_depth - base_depth >= max_depth:
                    del dirs[:]
                    continue
                for name in sorted(files):
                    if name.lower().endswith('.pdf'):
                        full = os.path.join(root, name)
                        rel_path = os.path.relpath(full, mount_path)
                        try:
                            size = os.path.getsize(full)
                        except OSError:
                            size = 0
                        pdfs.append({
                            'name': name,
                            'size': size,
                            'path': rel_path,
                        })
        else:
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

    @classmethod
    def get_file_path(cls, relative_path):
        """获取文件的绝对路径，自动定位到正确的挂载点"""
        mount_path = cls._find_mount_path_for_file(relative_path)
        if not mount_path:
            raise ValueError(f"Cannot find mount point for: {relative_path}")

        full = os.path.join(mount_path, relative_path)
        real_full = os.path.realpath(full)
        real_mount = os.path.realpath(mount_path)
        if not real_full.startswith(real_mount):
            raise ValueError("Invalid file path")
        return real_full

    @staticmethod
    def _extract_number(dirname):
        """从目录名中提取连续数字"""
        import re
        match = re.search(r'\d+', dirname)
        return int(match.group()) if match else 0

    @staticmethod
    def get_config():
        """兼容旧接口"""
        cfg = SMBManager._get_common_config()
        return {
            'server': cfg['server'],
            'share': SystemConfig.get('smb_share', 'abb/FS/10/D$/tbmdata/data/ftpdata'),
            'username': cfg['username'],
            'password': cfg['password'],
            'domain': cfg['domain'],
            'mount_path': SMBManager.get_mount_path(),
        }
