import os
from app.smb import SMBManager
from app.vision import InfoExtractor, InstructionClassifier, VisionOCRClient
from app.vision.models import TempFile, DesignCache, ScannedDirectory
from app.models import ScanProgress, ScannedFile


class Scanner:
    def __init__(self):
        self.smb = SMBManager()
        self.extractor = InfoExtractor()
        self.classifier = InstructionClassifier()
        self.ocr = VisionOCRClient()

    def scan_all(self):
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

            # 递归获取所有PDF（限制深度5层）
            pdfs = self.smb.list_pdfs(dirname, recursive=True, max_depth=5)

            if not pdfs:
                print(f"[INFO] 项目 {dirname} 没有PDF文件")
                ScannedDirectory.create_or_update(
                    dirname,
                    status='pending',
                    total_files=0,
                )
                continue

            print(f"[INFO] 项目 {dirname} 找到 {len(pdfs)} 个PDF")

            # 按设计编号分组
            pdf_groups = self._group_by_design_number(pdfs, dirname)

            for group_idx, (design_number, group_pdfs) in enumerate(pdf_groups.items()):
                # 检查是否应跳过（跨项目缓存）
                if DesignCache.should_skip(design_number):
                    print(f"[SKIP] 设计编号 {design_number} 已在其他项目标记，跳过 {len(group_pdfs)} 个文件")
                    scanned += len(group_pdfs)
                    continue

                # 处理该设计编号下的所有文件
                for pdf in group_pdfs:
                    current = ScanProgress.get()
                    if current['status'] == 'paused':
                        ScanProgress.update(
                            current_dir=dirname,
                            current_file=pdf['path'],
                            dir_index=dir_idx,
                            file_index=group_idx,
                            scanned_files=scanned,
                            matched_files=matched,
                        )
                        return {'status': 'paused'}

                    try:
                        self._process_single_pdf(pdf, dirname)
                        scanned += 1
                    except Exception as e:
                        print(f"Error processing {pdf['path']}: {e}")
                        scanned += 1

                    ScanProgress.update(
                        current_dir=dirname,
                        current_file=pdf['path'],
                        dir_index=dir_idx,
                        file_index=group_idx,
                        scanned_files=scanned,
                        matched_files=matched,
                    )

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

    def _group_by_design_number(self, pdfs, dirname):
        """按设计编号分组PDF，先提取信息再分组"""
        groups = {}
        for pdf in pdfs:
            file_path = self.smb.get_file_path(pdf['path'])
            try:
                info = self.extractor.extract_with_ocr_fallback(file_path, self.ocr)
                design_number = info['设计编号']
            except Exception as e:
                print(f"提取设计编号失败 {pdf['path']}: {e}")
                design_number = 'unknown'

            if design_number not in groups:
                groups[design_number] = []
            groups[design_number].append(pdf)

            # 先保存到临时库
            try:
                TempFile.create(
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
                print(f"保存临时文件失败 {pdf['path']}: {e}")

        return groups

    def _process_single_pdf(self, pdf, dirname):
        """处理单个 PDF 文件"""
        file_path = self.smb.get_file_path(pdf['path'])

        # 获取临时文件记录
        temp_file = TempFile.get_by_path(pdf['path'])
        if not temp_file:
            # 如果临时库没有，先提取信息
            info = self.extractor.extract_with_ocr_fallback(file_path, self.ocr)
            temp_file = TempFile.create(
                file_path=pdf['path'],
                directory=dirname,
                filename=pdf['name'],
                file_size=pdf['size'],
                建设单位=info.get('建设单位'),
                工程名称=info.get('工程名称'),
                设计编号=info['设计编号'],
                图名=info.get('图名'),
                图号=info.get('图号'),
                图别=info.get('图别'),
                status='pending',
            )
        else:
            info = {
                '建设单位': temp_file.get('建设单位'),
                '工程名称': temp_file.get('工程名称'),
                '设计编号': temp_file.get('设计编号'),
                '图名': temp_file.get('图名'),
                '图号': temp_file.get('图号'),
                '图别': temp_file.get('图别'),
            }

        temp_id = temp_file['id']
        design_number = info['设计编号']

        # qwen-3 裁图判断是否为说明
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
        if is_instruction:
            TempFile.update(temp_id, is_instruction=True, status='instruction')
        else:
            TempFile.update(temp_id, is_instruction=False, status='not_instruction')
            TempFile.update(temp_id, status='completed')
            return

        # 是说明，检查设计编号是否已被标记
        cache = DesignCache.get(design_number)
        if cache and cache['has_instruction']:
            # 已标记，直接入正式库（不再 OCR 找【】）
            self._save_to_formal(pdf, dirname, info, '', is_instruction=True)
            TempFile.update(temp_id, status='completed')
            return

        # 未标记，OCR 找【】
        result = self.ocr.process_and_check(file_path)

        if result['has_brackets']:
            # 找到【】，标记设计编号，保存正式库
            DesignCache.create_or_update(
                design_number,
                建设单位=info.get('建设单位'),
                工程名称=info.get('工程名称'),
                has_instruction=True,
                instruction_count=1,
                first_seen_directory=dirname,
            )
            self._save_to_formal(
                pdf, dirname, info,
                result['md_content'],
                is_instruction=True,
                ocr_task_id=result['task_id'],
            )
        # 没找到【】，留在临时库

        TempFile.update(temp_id, status='completed')

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
