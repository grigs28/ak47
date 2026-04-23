import os
from app.smb import SMBManager
from app.vision import InfoExtractor, InstructionClassifier, VisionOCRClient
from app.vision.models import TempFile, DesignCache
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

        dirs = self.smb.list_dirs()
        total_dirs = len(dirs)

        total_files = 0
        for dirname, _ in dirs:
            pdfs = self.smb.list_pdfs(dirname)
            total_files += len(pdfs)

        ScanProgress.update(
            status='running',
            total_dirs=total_dirs,
            total_files=total_files,
            started_at='CURRENT_TIMESTAMP',
        )

        start_dir_idx = progress.get('dir_index', 0)
        start_file_idx = progress.get('file_index', 0)

        scanned = progress.get('scanned_files', 0)
        matched = progress.get('matched_files', 0)

        for dir_idx in range(start_dir_idx, total_dirs):
            dirname, _ = dirs[dir_idx]
            pdfs = self.smb.list_pdfs(dirname)

            for file_idx in range(start_file_idx, len(pdfs)):
                current = ScanProgress.get()
                if current['status'] == 'paused':
                    ScanProgress.update(
                        current_dir=dirname,
                        current_file=pdfs[file_idx]['path'],
                        dir_index=dir_idx,
                        file_index=file_idx,
                        scanned_files=scanned,
                        matched_files=matched,
                    )
                    return {'status': 'paused'}

                pdf = pdfs[file_idx]
                file_path = self.smb.get_file_path(pdf['path'])

                try:
                    self._process_single_pdf(pdf, dirname, file_path)
                    scanned += 1

                    ScanProgress.update(
                        current_dir=dirname,
                        current_file=pdf['path'],
                        dir_index=dir_idx,
                        file_index=file_idx,
                        scanned_files=scanned,
                        matched_files=matched,
                    )

                except Exception as e:
                    print(f"Error processing {pdf['path']}: {e}")
                    scanned += 1
                    ScanProgress.update(
                        current_dir=dirname,
                        current_file=pdf['path'],
                        dir_index=dir_idx,
                        file_index=file_idx,
                        scanned_files=scanned,
                    )

            start_file_idx = 0

        ScanProgress.update(
            status='completed',
            completed_at='CURRENT_TIMESTAMP',
        )

        return {'status': 'completed', 'scanned': scanned, 'matched': matched}

    def _process_single_pdf(self, pdf, dirname, file_path):
        """处理单个 PDF 文件"""
        # 1. 提取 6 字段（qwen-3 + OCR 兜底）
        info = self.extractor.extract_with_ocr_fallback(file_path, self.ocr)
        design_number = info['设计编号']

        # 2. 保存到临时库
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
        temp_id = temp_file['id']

        # 3. qwen-3 裁图判断是否为说明
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
            # 不是说明，标记完成跳过
            TempFile.update(temp_id, status='completed')
            return

        # 4. 是说明，检查设计编号是否已被标记
        cache = DesignCache.get(design_number)
        if cache and cache['has_instruction']:
            # 已标记，直接入正式库（不再 OCR 找【】）
            self._save_to_formal(pdf, dirname, info, '', is_instruction=True)
            TempFile.update(temp_id, status='completed')
            return

        # 5. 未标记，OCR 找【】
        result = self.ocr.process_and_check(file_path)

        if result['has_brackets']:
            # 找到【】，标记设计编号，保存正式库
            DesignCache.create_or_update(
                design_number,
                建设单位=info.get('建设单位'),
                工程名称=info.get('工程名称'),
                has_instruction=True,
                instruction_count=1,
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
            scanned_at='CURRENT_TIMESTAMP',
        )
