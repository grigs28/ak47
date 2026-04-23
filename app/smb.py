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
        """列出挂载根目录下所有子目录，返回 [(dirname, number), ...]"""
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
