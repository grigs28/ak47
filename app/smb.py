import os
import subprocess
import shutil
from datetime import datetime
from app.models import SystemConfig

class SMBManager:
    _LOCKED_MOUNT_PATH = None

    @classmethod
    def get_mount_path(cls):
        # 如果已锁定，返回锁定路径
        if cls._LOCKED_MOUNT_PATH:
            return cls._LOCKED_MOUNT_PATH

        # 从数据库读取，如果没有则生成默认路径
        path = SystemConfig.get('smb_mount_path')
        if not path:
            path = cls._generate_default_path()
            SystemConfig.set('smb_mount_path', path)
        return path

    @staticmethod
    def _generate_default_path():
        ts = datetime.now().strftime('%Y%m%d_%H%M%S')
        home = os.path.expanduser('~')
        return os.path.join(home, 'mnt', 'ak47', ts)

    @classmethod
    def _lock_mount_path(cls, path):
        """锁定挂载点，禁止修改"""
        cls._LOCKED_MOUNT_PATH = path

    @staticmethod
    def get_config():
        return {
            'server': SystemConfig.get('smb_server', '192.168.0.79'),
            'share': SystemConfig.get('smb_share', 'abb/FS/10/D$/tbmdata/data/ftpdata'),
            'username': SystemConfig.get('smb_username', ''),
            'password': SystemConfig.get('smb_password', ''),
            'domain': SystemConfig.get('smb_domain', ''),
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

        # 检查是否已挂载
        if cls.is_mounted():
            cls._lock_mount_path(mount_path)
            return True

        # 未挂载：生成新的时间戳路径
        mount_path = cls._generate_default_path()
        SystemConfig.set('smb_mount_path', mount_path)

        os.makedirs(mount_path, exist_ok=True)

        # 处理 Windows UNC 格式：去掉开头的 \\\\ 和反斜杠
        share = cfg['share'].lstrip('\\').replace('\\', '/')
        share_url = f"//{cfg['server']}/{share}"
        opts = f"vers=3.0,sec=ntlmssp,username={cfg['username']},password={cfg['password']},uid={os.getuid()},gid={os.getgid()},file_mode=0777,dir_mode=0777,ro"
        if cfg['domain']:
            opts += f",domain={cfg['domain']}"
        cmd = [
            'sudo', '-S', 'mount', '-t', 'cifs',
            share_url,
            mount_path,
            '-o', opts
        ]

        result = subprocess.run(cmd, capture_output=True, text=True, input='Slnwg123$\n')
        if result.returncode != 0:
            raise RuntimeError(f"SMB mount failed: {result.stderr}")

        # 挂载成功后锁定路径
        cls._lock_mount_path(mount_path)
        return True

    @classmethod
    def umount(cls):
        mount_path = cls.get_mount_path()
        if not os.path.ismount(mount_path):
            return True

        result = subprocess.run(['sudo', '-S', 'umount', mount_path], capture_output=True, text=True, input='Slnwg123$\n')
        return result.returncode == 0

    @classmethod
    def list_dirs(cls):
        """列出挂载根目录下所有子目录，按修改时间降序排列"""
        mount_path = cls.get_mount_path()
        if not cls.is_mounted():
            raise RuntimeError("SMB not mounted")

        dirs = []
        for name in os.listdir(mount_path):
            full = os.path.join(mount_path, name)
            if os.path.isdir(full):
                mtime = os.path.getmtime(full)
                dirs.append((name, mtime))

        # 按修改时间降序排列（最新的在前面）
        dirs.sort(key=lambda x: x[1], reverse=True)
        return dirs

    @classmethod
    def list_pdfs(cls, directory):
        """列出指定目录下所有 PDF 文件"""
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
        """从目录名中提取连续数字"""
        import re
        match = re.search(r'\d+', dirname)
        return int(match.group()) if match else 0

    @classmethod
    def get_file_path(cls, relative_path):
        """获取文件的绝对路径（安全检查）"""
        mount_path = cls.get_mount_path()
        full = os.path.join(mount_path, relative_path)
        real_full = os.path.realpath(full)
        real_mount = os.path.realpath(mount_path)
        if not real_full.startswith(real_mount):
            raise ValueError("Invalid file path")
        return real_full
