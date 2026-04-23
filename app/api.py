from flask import Blueprint, request, jsonify, session
from app.auth import admin_required
from app.models import ScanProgress, ScannedFile, SystemConfig, OperationLog
from app.vision.models import TempFile, DesignCache, ScannedDirectory
from app.tasks import scan_task
from app.smb import SMBManager
from app.ocr import OCRClient
from app.ai import AIMatcher
from app.db import close_db

bp = Blueprint('api', __name__)

@bp.teardown_request
def teardown(e=None):
    close_db(e)

# === 扫描控制 ===

@bp.route('/scan/start', methods=['POST'])
@admin_required
def scan_start():
    progress = ScanProgress.get()
    if progress['status'] == 'running':
        return jsonify({'error': '扫描已在运行'}), 400

    # 自动启动 Celery Worker（如果未运行）
    import subprocess
    try:
        result = subprocess.run(['pgrep', '-f', 'celery.*celery_worker'], capture_output=True, text=True)
        if not result.stdout.strip():
            # Worker 未运行，自动启动
            import os
            env = os.environ.copy()
            env['DB_PASSWORD'] = os.environ.get('DB_PASSWORD', 'Slnwg123$')
            subprocess.Popen(
                ['celery', '-A', 'celery_worker', 'worker', '-l', 'info', '-c', '1'],
                stdout=open('/tmp/celery.log', 'a'),
                stderr=subprocess.STDOUT,
                env=env,
                start_new_session=True,
            )
    except Exception:
        pass  # 启动失败不影响主流程

    ScanProgress.update(status='running')
    task = scan_task.delay()

    OperationLog.create(
        user_id=session.get('user_id', 0),
        username=session.get('username', ''),
        action='start_scan',
        detail={'task_id': task.id},
    )

    return jsonify({'task_id': task.id, 'message': '扫描已启动'})

@bp.route('/scan/pause', methods=['POST'])
@admin_required
def scan_pause():
    progress = ScanProgress.get()
    if progress['status'] != 'running':
        return jsonify({'error': '扫描未在运行'}), 400

    ScanProgress.update(status='paused', paused_at='NOW()')

    OperationLog.create(
        user_id=session.get('user_id', 0),
        username=session.get('username', ''),
        action='pause_scan',
    )

    return jsonify({'message': '扫描已暂停'})

@bp.route('/scan/resume', methods=['POST'])
@admin_required
def scan_resume():
    progress = ScanProgress.get()
    if progress['status'] != 'paused':
        return jsonify({'error': '扫描未在暂停状态'}), 400

    ScanProgress.update(status='running')
    task = scan_task.delay()

    OperationLog.create(
        user_id=session.get('user_id', 0),
        username=session.get('username', ''),
        action='resume_scan',
        detail={'task_id': task.id},
    )

    return jsonify({'task_id': task.id, 'message': '扫描已恢复'})

@bp.route('/scan/reset', methods=['POST'])
@admin_required
def scan_reset():
    ScanProgress.reset()

    OperationLog.create(
        user_id=session.get('user_id', 0),
        username=session.get('username', ''),
        action='reset_scan',
    )

    return jsonify({'message': '扫描进度已重置'})

@bp.route('/scan/status', methods=['GET'])
@admin_required
def scan_status():
    progress = ScanProgress.get()
    return jsonify(progress)

# === 文件管理 ===

@bp.route('/files', methods=['GET'])
@admin_required
def file_list():
    page = request.args.get('page', 1, type=int)
    size = request.args.get('size', 20, type=int)
    directory = request.args.get('directory', None)
    selected = request.args.get('selected', None)
    if selected is not None:
        selected = selected.lower() == 'true'
    ai_matched = request.args.get('ai_matched', None)
    if ai_matched is not None:
        ai_matched = ai_matched.lower() == 'true'
    design_number = request.args.get('design_number', None)

    rows, total = ScannedFile.list(
        directory=directory,
        selected=selected,
        ai_matched=ai_matched,
        design_number=design_number,
        page=page,
        size=size,
    )

    return jsonify({
        'files': rows,
        'total': total,
        'page': page,
        'size': size,
    })

@bp.route('/files/<int:file_id>', methods=['GET'])
@admin_required
def file_detail(file_id):
    row = ScannedFile.get(file_id)
    if not row:
        return jsonify({'error': '文件不存在'}), 404
    return jsonify(row)

@bp.route('/files/<int:file_id>/select', methods=['POST'])
@admin_required
def file_select(file_id):
    data = request.get_json() or {}
    selected = data.get('selected', True)

    ScannedFile.update(file_id, selected=selected)

    OperationLog.create(
        user_id=session.get('user_id', 0),
        username=session.get('username', ''),
        action='select_file' if selected else 'deselect_file',
        detail={'file_id': file_id},
    )

    return jsonify({'message': '已更新'})

@bp.route('/files/batch-select', methods=['POST'])
@admin_required
def file_batch_select():
    data = request.get_json() or {}
    file_ids = data.get('file_ids', [])
    selected = data.get('selected', True)

    for fid in file_ids:
        ScannedFile.update(fid, selected=selected)

    return jsonify({'message': f'已更新 {len(file_ids)} 个文件'})

@bp.route('/files/<int:file_id>/preview', methods=['GET'])
@admin_required
def file_preview(file_id):
    from flask import send_file
    import os

    row = ScannedFile.get(file_id)
    if not row:
        return jsonify({'error': '文件不存在'}), 404

    try:
        file_path = SMBManager.get_file_path(row['file_path'])
        return send_file(file_path, mimetype='application/pdf')
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@bp.route('/files/<int:file_id>/md', methods=['GET'])
@admin_required
def file_download_md(file_id):
    from flask import Response

    row = ScannedFile.get(file_id)
    if not row:
        return jsonify({'error': '文件不存在'}), 404

    content = row.get('md_content', '')
    filename = row['filename'].replace('.pdf', '.md')

    return Response(
        content,
        mimetype='text/markdown',
        headers={'Content-Disposition': f'attachment; filename={filename}'},
    )

# === 目录浏览 ===

@bp.route('/browse', methods=['GET'])
@admin_required
def browse():
    path = request.args.get('path', '')
    page = request.args.get('page', 1, type=int)
    size = request.args.get('size', 100, type=int)

    try:
        if not path:
            dirs, total = SMBManager.list_dirs(page=page, size=size)
            # 获取目录状态
            items = []
            for d in dirs:
                name = d[0]
                status_row = ScannedDirectory.get(name)
                status = status_row['status'] if status_row else 'pending'
                items.append({
                    'name': name,
                    'mtime': d[1],
                    'type': 'directory',
                    'status': status,
                })
            return jsonify({
                'type': 'root',
                'items': items,
                'total': total,
                'page': page,
                'size': size,
            })
        else:
            pdfs = SMBManager.list_pdfs(path)
            return jsonify({
                'type': 'directory',
                'path': path,
                'items': [{'name': p['name'], 'size': p['size'], 'type': 'pdf'} for p in pdfs],
            })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# === 配置管理 ===

@bp.route('/config', methods=['GET'])
@admin_required
def config_list():
    rows = SystemConfig.all()
    return jsonify({'configs': rows})

@bp.route('/config/<key>', methods=['PUT'])
@admin_required
def config_update(key):
    data = request.get_json() or {}
    value = data.get('value')

    if value is None:
        return jsonify({'error': '缺少 value'}), 400

    SystemConfig.set(key, value)

    OperationLog.create(
        user_id=session.get('user_id', 0),
        username=session.get('username', ''),
        action='update_config',
        detail={'key': key},
    )

    return jsonify({'message': '配置已更新'})

@bp.route('/config/test-smb', methods=['POST'])
@admin_required
def test_smb():
    try:
        if SMBManager.is_mounted():
            SMBManager.umount()
            return jsonify({'success': False, 'message': 'SMB 已卸载'})
        SMBManager.mount()
        is_mounted = SMBManager.is_mounted()
        return jsonify({'success': is_mounted, 'message': 'SMB 挂载成功' if is_mounted else 'SMB 挂载失败'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

@bp.route('/config/smb-status', methods=['GET'])
@admin_required
def smb_status():
    try:
        is_mounted = SMBManager.is_mounted()
        return jsonify({'mounted': is_mounted})
    except Exception as e:
        return jsonify({'mounted': False, 'error': str(e)}), 500

@bp.route('/config/test-ocr', methods=['POST'])
@admin_required
def test_ocr():
    try:
        client = OCRClient()
        return jsonify({'success': True, 'base_url': client.base_url})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

@bp.route('/config/test-ai', methods=['POST'])
@admin_required
def test_ai():
    try:
        matcher = AIMatcher()
        result = matcher.match("【绿色建筑评价标准】测试内容")
        return jsonify({'success': True, 'result': result})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

@bp.route('/config/test-db', methods=['POST'])
@admin_required
def test_db():
    try:
        import psycopg2
        from app.models import SystemConfig
        host = SystemConfig.get('db_host', '192.168.0.98')
        port = int(SystemConfig.get('db_port', '5432'))
        dbname = SystemConfig.get('db_name', 'yz_relay')
        user = SystemConfig.get('db_user', 'grigs')
        password = SystemConfig.get('db_password', '')
        conn = psycopg2.connect(host=host, port=port, dbname=dbname, user=user, password=password)
        conn.close()
        return jsonify({'success': True, 'message': f'数据库连接成功 ({host}:{port}/{dbname})'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

@bp.route('/logs', methods=['GET'])
@admin_required
def log_list():
    page = request.args.get('page', 1, type=int)
    size = request.args.get('size', 50, type=int)
    rows = OperationLog.list(page=page, size=size)
    return jsonify({'logs': rows})

# === 临时库 ===

@bp.route('/temp-files', methods=['GET'])
@admin_required
def temp_file_list():
    page = request.args.get('page', 1, type=int)
    size = request.args.get('size', 20, type=int)
    design_number = request.args.get('design_number', None)
    status = request.args.get('status', None)

    rows, total = TempFile.list(
        design_number=design_number,
        status=status,
        page=page,
        size=size,
    )

    return jsonify({
        'files': rows,
        'total': total,
        'page': page,
        'size': size,
    })

@bp.route('/temp-files/<int:file_id>', methods=['GET'])
@admin_required
def temp_file_detail(file_id):
    row = TempFile.get(file_id)
    if not row:
        return jsonify({'error': '文件不存在'}), 404
    return jsonify(row)

@bp.route('/temp-files/<int:file_id>/classify', methods=['POST'])
@admin_required
def temp_file_classify(file_id):
    """手动触发分类（重新判断是否为说明）"""
    row = TempFile.get(file_id)
    if not row:
        return jsonify({'error': '文件不存在'}), 404

    # 重新分类逻辑（调用 classifier）
    from app.vision import InstructionClassifier
    from app.vision.utils import pdf_page_to_image, crop_image_region, get_crop_strategy
    from app.smb import SMBManager

    file_path = SMBManager.get_file_path(row['file_path'])
    image_path = pdf_page_to_image(file_path, page=1, dpi=200)
    strategies = get_crop_strategy(image_path)

    classifier = InstructionClassifier()
    is_instruction = False
    for region in strategies:
        crop_path = crop_image_region(image_path, region=region)
        is_instruction, confidence = classifier.classify(crop_path)
        if is_instruction:
            break

    status = 'instruction' if is_instruction else 'not_instruction'
    TempFile.update(file_id, is_instruction=is_instruction, status=status)

    return jsonify({'is_instruction': is_instruction, 'status': status})

# === 设计编号缓存 ===

@bp.route('/design-cache', methods=['GET'])
@admin_required
def design_cache_list():
    page = request.args.get('page', 1, type=int)
    size = request.args.get('size', 20, type=int)

    rows, total = DesignCache.list(page=page, size=size)

    return jsonify({
        'items': rows,
        'total': total,
        'page': page,
        'size': size,
    })

@bp.route('/design-cache/<design_number>', methods=['GET'])
@admin_required
def design_cache_detail(design_number):
    row = DesignCache.get(design_number)
    if not row:
        return jsonify({'error': '设计编号不存在'}), 404
    return jsonify(row)
