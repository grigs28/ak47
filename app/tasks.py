import os
from app import celery
from app.scan import Scanner
from app.models import ScanProgress, SystemConfig
from app.db import execute


@celery.task(bind=True, max_retries=3)
def scan_task(self):
    """后台扫描任务：遍历目录 + 派发PDF任务"""
    # 先卸载再重新挂载 SMB，确保使用最新配置
    from app.smb import SMBManager
    try:
        SMBManager.umount_all()
    except Exception:
        pass
    try:
        SMBManager.mount_all()
        print(f"[INFO] SMB 挂载成功")
    except Exception as e:
        print(f"[WARN] SMB 挂载失败: {e}")

    scanner = Scanner()

    try:
        result = scanner.scan_all()
        return result
    except Exception as exc:
        # 如果暂停，不重试
        progress = ScanProgress.get()
        if progress['status'] == 'paused':
            return {'status': 'paused'}

        # 其他错误重试
        raise self.retry(exc=exc, countdown=10)


@celery.task(bind=True, max_retries=2)
def process_pdf_task(self, pdf, dirname):
    """处理单个PDF文件的Celery任务，由 prefork worker 执行
    pdf: {'name': ..., 'size': ..., 'path': ...}
    dirname: 目录名
    """
    from app.smb import SMBManager
    from app.vision import InfoExtractor, InstructionClassifier, VisionOCRClient
    from app.vision.models import TempFile, design_cache_memory
    from app.models import ScannedFile
    from app.db import get_conn
    import time

    t_start = time.time()
    filename = pdf['name']
    worker_id = os.getpid()

    # 检查暂停/重置
    progress = ScanProgress.get()
    if progress['status'] in ('idle', 'paused'):
        print(f"[Worker {worker_id}] {filename} | 任务取消，状态={progress['status']}")
        return {'status': 'cancelled'}

    try:
        file_path = SMBManager.get_file_path(pdf['path'])
    except Exception as e:
        print(f"[Worker {worker_id}] {filename} | 路径解析失败 | {e}")
        _increment_scanned(dirname)
        return {'status': 'error', 'error': str(e)}

    # ====== 步骤0+1: 提取6字段 ======
    try:
        extractor = InfoExtractor()
        info = extractor.extract(file_path)
        design_number = info.get('设计编号', 'unknown')
        source = info.get('source', '?')
    except Exception as e:
        elapsed = time.time() - t_start
        print(f"[Worker {worker_id}] {filename} | 提取失败 | {elapsed:.1f}s | {e}")
        _increment_scanned(dirname)
        return {'status': 'error', 'error': str(e)}

    # 文本路径字段不全 → 跳过
    if source == 'text' and not info.get('is_instruction') and not info.get('建设单位'):
        elapsed = time.time() - t_start
        print(f"[Worker {worker_id}] {filename} | 字段不全 | {source} | {elapsed:.1f}s | 跳过")
        _increment_scanned(dirname)
        return {'status': 'skipped'}

    # ====== 步骤2: 判断是否说明 ======
    is_instruction = info.get('is_instruction', False)

    # 视觉路径：分类器判断
    if source == 'vision':
        from app.vision.utils import pdf_page_to_image, crop_image_region, get_crop_strategy
        classifier = InstructionClassifier()
        image_path = pdf_page_to_image(file_path, page=1, dpi=200)
        strategies = get_crop_strategy(image_path)
        for region in strategies:
            crop_path = crop_image_region(image_path, region=region)
            is_instruction, confidence = classifier.classify(crop_path)
            if is_instruction:
                print(f"[Worker {worker_id}] {filename} | 分类器=说明 | 区域={region} | 置信度={confidence:.2f}")
                break

    # 文本路径没找到说明 → VL 视觉分类器兜底
    if not is_instruction and source == 'text':
        from app.vision.utils import pdf_page_to_image, crop_image_region, get_crop_strategy
        classifier = InstructionClassifier()
        image_path = pdf_page_to_image(file_path, page=1, dpi=200)
        strategies = get_crop_strategy(image_path)
        for region in strategies:
            crop_path = crop_image_region(image_path, region=region)
            is_instruction, confidence = classifier.classify(crop_path)
            if is_instruction:
                print(f"[Worker {worker_id}] {filename} | VL兜底=说明 | 区域={region} | 置信度={confidence:.2f}")
                break

    # 不是说明 → 跳过
    if not is_instruction:
        elapsed = time.time() - t_start
        print(f"[Worker {worker_id}] {filename} | 非说明 | {source} | {elapsed:.1f}s | 设计编号={design_number} | 跳过")
        _increment_scanned(dirname)
        return {'status': 'skipped'}

    # ====== 是说明，保存临时库 ======
    temp_id = None
    try:
        temp_file = TempFile.get_by_path(pdf['path'])
        if not temp_file:
            temp_file = TempFile.create(
                file_path=pdf['path'],
                directory=dirname,
                filename=pdf['name'],
                file_size=pdf['size'],
                建设单位=info.get('建设单位'),
                工程名称=info.get('工程名称'),
                设计编号=design_number,
                图名=info.get('图名'),
                图号=info.get('图号'),
                图别=info.get('图别'),
                is_instruction=True,
                status='instruction',
            )
        temp_id = temp_file['id'] if temp_file else None
    except Exception as e:
        print(f"[Worker {worker_id}] 保存临时文件失败 {pdf['path']}: {e}")

    # ====== 步骤3: 标准名称匹配 ======
    # 先检查设计编号缓存：该设计编号已有匹配 → 跳过标准检查
    if design_cache_memory.should_skip(design_number):
        standard_match = True
        print(f"[Worker {worker_id}] {filename} | 设计编号缓存命中 | 跳过标准匹配 | 设计编号={design_number}")
    else:
        standard_match = _check_standard_match(file_path)

    if not standard_match:
        elapsed = time.time() - t_start
        print(f"[Worker {worker_id}] {filename} | 是说明但标准不匹配 | 留临时库 | {elapsed:.1f}s | 设计编号={design_number}")
        _increment_scanned(dirname)
        return {'status': 'no_standard_match'}

    # ====== 步骤4: 标准匹配 → OCR入库 ======
    ocr = VisionOCRClient()

    try:
        task_id, md_content = ocr.process_file(file_path)
        _save_to_formal(pdf, dirname, info, md_content, is_instruction=True, ocr_task_id=task_id)
        if temp_id:
            TempFile.delete(temp_id)
        design_cache_memory.mark(design_number)
        # 迁移同设计编号的临时文件到正式库
        promoted = _promote_temp_files(design_number, dirname)
        if promoted > 0:
            print(f"[Worker {worker_id}] {filename} | 迁移 {promoted} 个同设计编号文件到正式库 | 设计编号={design_number}")
        elapsed_total = time.time() - t_start
        print(f"[Worker {worker_id}] {filename} | 标准匹配→OCR入库 | 总耗时={elapsed_total:.1f}s | 设计编号={design_number}")
        _increment_matched(dirname)
        return {'status': 'matched'}
    except Exception as e:
        elapsed_total = time.time() - t_start
        print(f"[Worker {worker_id}] {filename} | OCR失败 | {elapsed_total:.1f}s | {e}")
        if temp_id:
            TempFile.delete(temp_id)
        _increment_scanned(dirname)
        return {'status': 'ocr_error'}


def _increment_scanned(dirname):
    """原子递增 scanned_files 计数器"""
    execute(
        "UPDATE scan_progress SET scanned_files = COALESCE(scanned_files, 0) + 1, "
        "current_dir = %s, updated_at = CURRENT_TIMESTAMP WHERE id = 1",
        (dirname,)
    )


def _increment_matched(dirname):
    """原子递增 scanned_files 和 matched_files 计数器"""
    execute(
        "UPDATE scan_progress SET "
        "scanned_files = COALESCE(scanned_files, 0) + 1, "
        "matched_files = COALESCE(matched_files, 0) + 1, "
        "current_dir = %s, updated_at = CURRENT_TIMESTAMP WHERE id = 1",
        (dirname,)
    )


def _promote_temp_files(design_number, dirname):
    """将临时库中同设计编号的说明文件迁移到正式库（OCR + 入库 + 删除临时记录）"""
    from app.vision.models import TempFile
    from app.vision import VisionOCRClient
    from app.smb import SMBManager
    from app.models import ScannedFile
    from app.db import query

    rows = query(
        "SELECT * FROM temp_files WHERE 设计编号 = %s AND is_instruction = TRUE",
        (design_number,), fetchall=True
    )
    if not rows:
        return 0

    ocr = VisionOCRClient()
    promoted = 0

    for row in rows:
        try:
            # 已在正式库则跳过
            existing = ScannedFile.get_by_path(row['file_path'])
            if existing:
                TempFile.delete(row['id'])
                continue

            file_path = SMBManager.get_file_path(row['file_path'])

            # OCR 处理
            task_id, md_content = ocr.process_file(file_path)

            info = {
                '建设单位': row.get('建设单位'),
                '工程名称': row.get('工程名称'),
                '设计编号': row.get('设计编号'),
                '图名': row.get('图名'),
                '图号': row.get('图号'),
                '图别': row.get('图别'),
            }
            _save_to_formal(
                {'name': row['filename'], 'size': row['file_size'], 'path': row['file_path']},
                row['directory'],
                info,
                md_content,
                is_instruction=True,
                ocr_task_id=task_id,
            )

            TempFile.delete(row['id'])
            promoted += 1
            print(f"[Promote] {row['filename']} | 设计编号={design_number} | 迁移成功")
        except Exception as e:
            print(f"[Promote] {row.get('filename', '?')} | 迁移失败: {e}")

    return promoted


def _check_standard_match(file_path):
    """检查PDF内容是否匹配配置的标准名称关键词
    流程：文本提取匹配 → 失败则 VL 视觉识别匹配
    """
    try:
        standard = SystemConfig.get('gbt_standard', '')
        if not standard:
            return True
        keywords = [kw.strip().lower() for kw in standard.split(',') if kw.strip()]
        if not keywords:
            return True

        # === 第1步：文本提取匹配 ===
        import pdfplumber
        text = ''
        with pdfplumber.open(file_path) as pdf:
            for page in pdf.pages[:3]:
                text += (page.extract_text() or '')

        compact_lower = text.replace(' ', '').replace('\u3000', '').lower()
        if all(kw in compact_lower for kw in keywords):
            return True

        # DEBUG: 打印匹配失败原因
        import os as _os
        fname = _os.path.basename(file_path)
        missing = [kw for kw in keywords if kw not in compact_lower]
        print(f"[DEBUG] 文本匹配失败: {fname} | text_len={len(text)} | missing_kw={missing}")
        if len(text) > 0:
            for kw in missing:
                # 找最接近的
                if '50378' in kw:
                    idx = compact_lower.find('50378')
                    if idx >= 0:
                        print(f"  found '50378' at pos {idx}: ...{compact_lower[max(0,idx-20):idx+20]}...")

        # === 第2步：VL 视觉识别匹配（文本不够或匹配失败）===
        return _vl_check_standard(file_path, keywords)
    except Exception as e:
        print(f"[DEBUG] _check_standard_match exception: {e}")
        return False


def _vl_check_standard(file_path, keywords):
    """用 VL 视觉识别从图纸中找标准号，匹配关键词"""
    import json, requests
    from app.vision.utils import pdf_page_to_image, image_to_base64

    base_url = SystemConfig.get('qwen_base_url', '')
    api_key = SystemConfig.get('qwen_api_key', '')
    model = SystemConfig.get('qwen_model', 'qwen-3')

    prompt = '请列出这张图纸中引用的所有国家标准编号（如GB/T xxxxx-xxxx），用逗号分隔返回，不要其他内容。'

    for page in [1, 2, 3]:
        try:
            img = pdf_page_to_image(file_path, page=page, dpi=200)
        except Exception:
            break
        b64 = image_to_base64(img)

        body = {
            'model': model,
            'messages': [{'role': 'user', 'content': [
                {'type': 'text', 'text': prompt},
                {'type': 'image_url', 'image_url': {'url': f'data:image/png;base64,{b64}'}},
            ]}],
            'temperature': 0.1,
            'max_tokens': 200,
        }
        think_enabled = SystemConfig.get('vl_think', 'false') == 'true'
        if not think_enabled:
            body['chat_template_kwargs'] = {'enable_thinking': False}

        try:
            resp = requests.post(
                f'{base_url}/chat/completions',
                headers={'Authorization': f'Bearer {api_key}', 'Content-Type': 'application/json'},
                json=body, timeout=60,
            )
            resp.raise_for_status()
            content = resp.json()['choices'][0]['message'].get('content', '')
            compact = content.replace(' ', '').replace('\u3000', '').lower()
            missing = [kw for kw in keywords if kw not in compact]
            print(f"[DEBUG] VL page={page} response: {content[:200]} | compact: {compact[:200]} | missing={missing}")
            if all(kw in compact for kw in keywords):
                return True
        except Exception as e:
            print(f"[DEBUG] VL page={page} exception: {e}")
            continue

    return False


def _save_to_formal(pdf, dirname, info, md_content, is_instruction=False, ocr_task_id=None):
    """保存到正式库"""
    import json
    from app.models import ScannedFile
    ScannedFile.create(
        file_path=pdf['path'],
        directory=dirname,
        filename=pdf['name'],
        file_size=pdf['size'],
        建设单位=info.get('建设单位'),
        工程名称=info.get('工程名称'),
        设计编号=info.get('设计编号'),
        图名=info.get('图名'),
        图号=info.get('图号'),
        图别=info.get('图别'),
        json_result=json.dumps(info),
        is_instruction=is_instruction,
        has_brackets=True,
        ocr_status='done',
        ocr_task_id=ocr_task_id,
        md_content=md_content,
        scanned_at='NOW()',
    )
