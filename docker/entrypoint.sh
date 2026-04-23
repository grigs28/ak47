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
