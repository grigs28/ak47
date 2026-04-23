import os
from app.smb import SMBManager
from app.ocr import OCRClient
from app.ai import AIMatcher
from app.models import ScanProgress, ScannedFile

class Scanner:
    def __init__(self):
        self.smb = SMBManager()
        self.ocr = OCRClient()
        self.ai = AIMatcher()

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
                    task_id, md_content = self.ocr.process_file(file_path)

                    has_brackets = self.ai.has_brackets(md_content)

                    scanned += 1

                    if has_brackets:
                        matched += 1

                        ai_result = self.ai.match(md_content)

                        ScannedFile.create(
                            file_path=pdf['path'],
                            directory=dirname,
                            filename=pdf['name'],
                            file_size=pdf['size'],
                            has_brackets=True,
                            ai_matched=ai_result.get('matched'),
                            ai_confidence=ai_result.get('confidence'),
                            ai_reason=ai_result.get('reason'),
                            ocr_status='done',
                            ocr_task_id=task_id,
                            md_content=md_content,
                            scanned_at='CURRENT_TIMESTAMP',
                        )

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
