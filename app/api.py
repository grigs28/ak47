from flask import Blueprint, request, jsonify, session
from app.auth import admin_required
from app.models import ScanProgress, ScannedFile, SystemConfig, OperationLog
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

    ScanProgress.update(status='paused', paused_at='CURRENT_TIMESTAMP')

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

    rows, total = ScannedFile.list(
        directory=directory,
        selected=selected,
        ai_matched=ai_matched,
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

    try:
        if not path:
            dirs = SMBManager.list_dirs()
            return jsonify({
                'type': 'root',
                'items': [{'name': d[0], 'number': d[1], 'type': 'directory'} for d in dirs],
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
        SMBManager.mount()
        is_mounted = SMBManager.is_mounted()
        return jsonify({'success': is_mounted, 'message': 'SMB 挂载成功' if is_mounted else 'SMB 挂载失败'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

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
