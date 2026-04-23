import os
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from app.smb import SMBManager
from app.vision import InfoExtractor, InstructionClassifier, VisionOCRClient
from app.vision.models import TempFile, DesignCache, ScannedDirectory, design_cache_memory
from app.models import ScanProgress, ScannedFile


class Scanner:
    def __init__(self):
        self.smb = SMBManager()
        self.extractor = InfoExtractor()
        self.classifier = InstructionClassifier()
        self.ocr = VisionOCRClient()
        # 线程锁，用于保护共享状态
        self._progress_lock = threading.Lock()
        self._design_lock = threading.Lock()

    def scan_all(self):
        # 加载设计编号缓存到内存
        design_cache_memory.load_from_db()

        progress = ScanProgress.get()

        if progress['status'] == 'completed':
            ScanProgress.reset()
            progress = ScanProgress.get()

        # 按 mtime 最新优先，每次取 20 个项目
        dirs, total_dirs = self.smb.list_dirs(page=1, size=20)

        # 快速估算文件数（不递归遍历）
        estimated_files = 0
        for dirname, _ in dirs:
            try:
                pdfs = self.smb.list_pdfs(dirname, recursive=False)
                estimated_files += len(pdfs)
            except Exception:
                pass

        ScanProgress.update(
            status='running',
            total_dirs=total_dirs,
            total_files=estimated_files,
            started_at='NOW()',
        )

        start_dir_idx = progress.get('dir_index', 0)

        scanned = progress.get('scanned_files', 0)
        matched = progress.get('matched_files', 0)

        for dir_idx in range(start_dir_idx, len(dirs)):
            dirname, _ = dirs[dir_idx]

            # 跳过已完成的项目
            if ScannedDirectory.is_completed(dirname):
                print(f"[SKIP] 项目 {dirname} 已完成，跳过")
                continue

            # 标记项目为扫描中
            ScannedDirectory.create_or_update(
                dirname,
                status='scanning',
                started_at='NOW()',
            )

            # 递归获取所有PDF（限制深度10层）
            pdfs = self.smb.list_pdfs(dirname, recursive=True, max_depth=10)

            if not pdfs:
                print(f"[INFO] 项目 {dirname} 没有PDF文件")
                ScannedDirectory.create_or_update(
                    dirname,
                    status='pending',
                    total_files=0,
                )
                continue

            print(f"[INFO] 项目 {dirname} 找到 {len(pdfs)} 个PDF，启动3线程扫描")

            # 更新总文件数（实际值）
            ScanProgress.update(total_files=len(pdfs))

            # === 多线程扫描 ===
            result = self._scan_directory_multi_thread(dirname, pdfs)

            if result['status'] == 'paused':
                # 暂停时保存进度
                with self._progress_lock:
                    ScanProgress.update(
                        current_dir=dirname,
                        dir_index=dir_idx,
                        scanned_files=result.get('scanned', scanned),
                        matched_files=result.get('matched', matched),
                    )
                return {'status': 'paused'}

            scanned += result.get('scanned', 0)
            matched += result.get('matched', 0)

            # 项目扫描完成，更新统计
            ScannedDirectory.create_or_update(
                dirname,
                status='pending',
                total_files=len(pdfs),
                scanned_files=scanned,
                matched_files=matched,
            )

        ScanProgress.update(
            status='completed',
            completed_at='NOW()',
        )

        return {'status': 'completed', 'scanned': scanned, 'matched': matched}

    def _scan_directory_multi_thread(self, dirname, pdfs):
        """多线程扫描单个目录，线程数由系统配置决定（默认3线程）"""
        from app.models import SystemConfig
        num_threads = int(SystemConfig.get('scan_threads', '3'))
        num_threads = max(1, min(num_threads, 5))  # 限制1-5线程

        # 将文件分成N组
        file_groups = self._split_files_for_threads(pdfs, num_threads)

        scanned = 0
        matched = 0
        paused = False

        print(f"[INFO] 项目 {dirname} 启动 {num_threads} 线程扫描")

        with ThreadPoolExecutor(max_workers=num_threads) as executor:
            # 提交N个线程任务
            futures = {
                executor.submit(self._thread_worker, dirname, files, thread_id): thread_id
                for thread_id, files in file_groups.items()
            }

            for future in as_completed(futures):
                thread_id = futures[future]
                try:
                    result = future.result()
                    if result.get('status') == 'paused':
                        paused = True
                    else:
                        scanned += result.get('scanned', 0)
                        matched += result.get('matched', 0)
                except Exception as e:
                    print(f"[ERROR] 线程 {thread_id} 异常: {e}")

        if paused:
            return {'status': 'paused', 'scanned': scanned, 'matched': matched}

        return {'status': 'completed', 'scanned': scanned, 'matched': matched}

    def _split_files_for_threads(self, pdfs, num_threads=3):
        """将PDF文件按范围分配给N个线程
        均分策略：每10个文件为一组，按线程数均分
        2线程: A(0-4), B(5-9)
        3线程: A(0-3), B(4-6), C(7-9)
        4线程: A(0-2), B(3-4), C(5-7), D(8-9)
        """
        # 生成线程ID列表
        thread_ids = [chr(ord('A') + i) for i in range(num_threads)]
        groups = {tid: [] for tid in thread_ids}

        # 每10个文件为一组，计算每个线程分到的数量
        files_per_cycle = 10
        files_per_thread = files_per_cycle // num_threads
        remainder = files_per_cycle % num_threads

        for idx, pdf in enumerate(pdfs):
            pos_in_cycle = idx % files_per_cycle

            # 计算该位置属于哪个线程
            assigned_thread = 0
            boundary = 0
            for t in range(num_threads):
                # 前 remainder 个线程多分1个
                chunk_size = files_per_thread + (1 if t < remainder else 0)
                boundary += chunk_size
                if pos_in_cycle < boundary:
                    assigned_thread = t
                    break

            groups[thread_ids[assigned_thread]].append(pdf)

        return groups

    def _thread_worker(self, dirname, files, thread_id):
        """线程工作函数"""
        scanned = 0
        matched = 0

        for pdf in files:
            # 检查暂停状态
            current = ScanProgress.get()
            if current['status'] == 'paused':
                with self._progress_lock:
                    ScanProgress.update(
                        current_dir=dirname,
                        current_file=pdf['path'],
                        scanned_files=scanned,
                        matched_files=matched,
                    )
                return {'status': 'paused', 'scanned': scanned, 'matched': matched}

            try:
                result = self._process_single_pdf_thread_safe(pdf, dirname, thread_id)
                scanned += 1
                if result:
                    matched += 1
            except Exception as e:
                print(f"[ERROR] 线程 {thread_id} 处理 {pdf['path']}: {e}")
                scanned += 1

            # 更新进度
            with self._progress_lock:
                ScanProgress.update(
                    current_dir=dirname,
                    current_file=pdf['path'],
                    scanned_files=scanned,
                    matched_files=matched,
                )

        return {'status': 'completed', 'scanned': scanned, 'matched': matched}

    def _process_single_pdf_thread_safe(self, pdf, dirname, thread_id):
        """线程安全的单PDF处理
        返回 True 表示匹配（是说明），False 表示不匹配
        """
        file_path = self.smb.get_file_path(pdf['path'])

        # 步骤1: 提取设计编号（每个线程独立）
        try:
            info = self.extractor.extract_with_ocr_fallback(file_path, self.ocr)
            design_number = info['设计编号']
        except Exception as e:
            print(f"[线程 {thread_id}] 提取设计编号失败 {pdf['path']}: {e}")
            design_number = 'unknown'
            info = {}

        # 保存到临时库
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
                    status='pending',
                )
        except Exception as e:
            print(f"[线程 {thread_id}] 保存临时文件失败 {pdf['path']}: {e}")

        temp_id = temp_file['id'] if temp_file else None

        # 步骤2: qwen-3 裁图判断是否为说明（每个线程独立）
        from app.vision.utils import pdf_page_to_image, crop_image_region, get_crop_strategy
        image_path = pdf_page_to_image(file_path, page=1, dpi=200)
        strategies = get_crop_strategy(image_path)

        is_instruction = False
        for region in strategies:
            crop_path = crop_image_region(image_path, region=region)
            is_instruction, confidence = self.classifier.classify(crop_path)
            if is_instruction:
                break

        # 更新临时库状态
        if temp_id:
            if is_instruction:
                TempFile.update(temp_id, is_instruction=True, status='instruction')
            else:
                TempFile.update(temp_id, is_instruction=False, status='not_instruction')
                TempFile.update(temp_id, status='completed')
                return False

        # 是说明，检查设计编号是否已被标记（内存缓存，线程安全）
        cache_hit = design_cache_memory.should_skip(design_number)

        if cache_hit:
            # 已标记，直接入正式库（不再 OCR 找【】，但要做 OCR 生成 MD）
            print(f"[线程 {thread_id}] 设计编号 {design_number} 已标记，直接OCR生成MD")
            try:
                task_id, md_content = self.ocr.process_file(file_path)
                self._save_to_formal(pdf, dirname, info, md_content, is_instruction=True, ocr_task_id=task_id)
                if temp_id:
                    TempFile.update(temp_id, status='completed')
                return True
            except Exception as e:
                print(f"[线程 {thread_id}] OCR生成MD失败 {pdf['path']}: {e}")
                if temp_id:
                    TempFile.update(temp_id, status='completed')
                return True

        # 未标记，需要 OCR 找【】（第一个文件）
        print(f"[线程 {thread_id}] 设计编号 {design_number} 首次出现，OCR找【】")
        result = self.ocr.process_and_check(file_path)

        if result['has_brackets']:
            # 找到【】，标记设计编号（线程安全）
            design_cache_memory.mark(design_number)
            print(f"[线程 {thread_id}] 找到【】，标记设计编号 {design_number}")

            self._save_to_formal(
                pdf, dirname, info,
                result['md_content'],
                is_instruction=True,
                ocr_task_id=result['task_id'],
            )
            if temp_id:
                TempFile.update(temp_id, status='completed')
            return True

        # 没找到【】，留在临时库
        if temp_id:
            TempFile.update(temp_id, status='completed')
        return False

    def _save_to_formal(self, pdf, dirname, info, md_content, is_instruction=False, ocr_task_id=None):
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

    def mark_project_completed(self, directory):
        """标记项目完成并清理临时文件"""
        # 1. 标记项目完成
        ScannedDirectory.mark_completed(directory)

        # 2. 删除该项目的临时文件
        from app.db import execute
        execute("DELETE FROM temp_files WHERE directory = %s", (directory,))

        return {'success': True, 'message': f'项目 {directory} 已完成，临时文件已清理'}
