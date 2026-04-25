import os
from app import celery
from app.scan import Scanner
from app.models import ScanProgress, SystemConfig
from app.db import execute


@celery.task(bind=True, max_retries=3)
def scan_task(self):
    """后台扫描任务：遍历目录 + 派发PDF任务"""
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
    standard_match = _check_standard_match(file_path)

    if not standard_match:
        elapsed = time.time() - t_start
        print(f"[Worker {worker_id}] {filename} | 是说明但标准不匹配 | 留临时库 | {elapsed:.1f}s | 设计编号={design_number}")
        _increment_scanned(dirname)
        return {'status': 'no_standard_match'}

    # ====== 步骤4: 标准匹配 → OCR入库 ======
    ocr = VisionOCRClient()
    cache_hit = design_cache_memory.should_skip(design_number)

    if cache_hit:
        try:
            task_id, md_content = ocr.process_file(file_path)
            _save_to_formal(pdf, dirname, info, md_content, is_instruction=True, ocr_task_id=task_id)
            if temp_id:
                TempFile.delete(temp_id)
            elapsed_total = time.time() - t_start
            print(f"[Worker {worker_id}] {filename} | 缓存命中→OCR入库 | 总耗时={elapsed_total:.1f}s")
            _increment_matched(dirname)
            return {'status': 'matched'}
        except Exception as e:
            elapsed_total = time.time() - t_start
            print(f"[Worker {worker_id}] {filename} | OCR失败 | {elapsed_total:.1f}s | {e}")
            if temp_id:
                TempFile.delete(temp_id)
            _increment_matched(dirname)
            return {'status': 'ocr_error'}

    # 未缓存 → OCR找【】
    print(f"[Worker {worker_id}] {filename} | 设计编号={design_number} 首次出现，OCR找【】")
    result = ocr.process_and_check(file_path)

    if result['has_brackets']:
        design_cache_memory.mark(design_number)
        _save_to_formal(
            pdf, dirname, info,
            result['md_content'],
            is_instruction=True,
            ocr_task_id=result['task_id'],
        )
        if temp_id:
            TempFile.delete(temp_id)
        elapsed_total = time.time() - t_start
        print(f"[Worker {worker_id}] {filename} | 找到【】标记={design_number}→入库 | 总耗时={elapsed_total:.1f}s")
        _increment_matched(dirname)
        return {'status': 'matched'}

    # 没找到【】→ 跳过，删临时
    elapsed_total = time.time() - t_start
    print(f"[Worker {worker_id}] {filename} | OCR未找到【】 | 跳过 | 总耗时={elapsed_total:.1f}s | 设计编号={design_number}")
    if temp_id:
        TempFile.delete(temp_id)
    _increment_scanned(dirname)
    return {'status': 'no_brackets'}


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


def _check_standard_match(file_path):
    """检查PDF内容是否匹配配置的标准名称关键词"""
    try:
        from app.vision import InfoExtractor
        standard = SystemConfig.get('gbt_standard', '')
        if not standard:
            return True
        keywords = [kw.strip().lower() for kw in standard.split(',') if kw.strip()]
        if not keywords:
            return True

        file_size = os.path.getsize(file_path)
        extractor = InfoExtractor()

        if file_size < extractor.VISION_SIZE_THRESHOLD:
            import pdfplumber
            with pdfplumber.open(file_path) as pdf:
                if not pdf.pages:
                    return False
                text = pdf.pages[0].extract_text() or ''
        else:
            from app.vision import VisionOCRClient
            ocr = VisionOCRClient()
            task_id, md_content = ocr.process_file(file_path)
            text = md_content or ''

        compact_lower = text.replace(' ', '').replace('\u3000', '').lower()
        return all(kw in compact_lower for kw in keywords)
    except Exception:
        return False


def _save_to_formal(pdf, dirname, info, md_content, is_instruction=False, ocr_task_id=None):
    """保存到正式库"""
    import json
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
