import os
import shutil
import atexit
import base64
from PIL import Image

TEMP_BASE = '/tmp/ak47_vision'


def get_temp_dir():
    """获取当前进程的临时目录"""
    pid = os.getpid()
    path = os.path.join(TEMP_BASE, str(pid))
    os.makedirs(path, exist_ok=True)
    return path


def cleanup_temp_dir():
    """清理当前进程的临时目录"""
    pid = os.getpid()
    path = os.path.join(TEMP_BASE, str(pid))
    if os.path.exists(path):
        shutil.rmtree(path, ignore_errors=True)


def cleanup_all_old_temp(max_age_hours=24):
    """清理超过 max_age_hours 的临时目录"""
    import time
    if not os.path.exists(TEMP_BASE):
        return
    now = time.time()
    for name in os.listdir(TEMP_BASE):
        path = os.path.join(TEMP_BASE, name)
        if os.path.isdir(path):
            mtime = os.path.getmtime(path)
            if (now - mtime) > max_age_hours * 3600:
                shutil.rmtree(path, ignore_errors=True)


def image_to_base64(image_path):
    """图片转 base64"""
    with open(image_path, 'rb') as f:
        return base64.b64encode(f.read()).decode('utf-8')


def pdf_page_to_image(pdf_path, page=1, dpi=200):
    """PDF 指定页转图片，返回图片路径"""
    import subprocess
    temp_dir = get_temp_dir()
    basename = os.path.splitext(os.path.basename(pdf_path))[0]
    output_prefix = os.path.join(temp_dir, f"{basename}_p{page}")

    cmd = [
        'pdftoppm', '-png', '-r', str(dpi),
        '-f', str(page), '-l', str(page),
        pdf_path, output_prefix
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"pdftoppm failed: {result.stderr}")

    # pdftoppm 输出格式: {prefix}-{page}.png
    output_path = f"{output_prefix}-{page}.png"
    if not os.path.exists(output_path):
        # 有时页码格式不同，尝试查找
        for name in os.listdir(temp_dir):
            if name.startswith(f"{basename}_p{page}") and name.endswith('.png'):
                output_path = os.path.join(temp_dir, name)
                break
    return output_path


def crop_image_region(image_path, region='bottom'):
    """裁剪图片区域
    region: 'bottom' | 'top' | 'right' | 'left'
    """
    img = Image.open(image_path)
    w, h = img.size

    if region == 'bottom':
        box = (0, int(h * 0.8), w, h)
    elif region == 'top':
        box = (0, 0, w, int(h * 0.2))
    elif region == 'right':
        box = (int(w * 0.8), 0, w, h)
    elif region == 'left':
        box = (0, 0, int(w * 0.2), h)
    else:
        box = (0, int(h * 0.8), w, h)

    cropped = img.crop(box)
    temp_dir = get_temp_dir()
    basename = os.path.splitext(os.path.basename(image_path))[0]
    output_path = os.path.join(temp_dir, f"{basename}_{region}.png")
    cropped.save(output_path, 'PNG')
    return output_path


def get_crop_strategy(image_path):
    """根据图片方向返回裁剪策略列表"""
    img = Image.open(image_path)
    w, h = img.size
    if h > w:
        # 竖版：先底部，再顶部
        return ['bottom', 'top']
    else:
        # 横版：先右侧，再左侧
        return ['right', 'left']


# 注册进程退出清理
atexit.register(cleanup_temp_dir)
