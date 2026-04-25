import os
import threading
from app.smb import SMBManager
from app.models import ScanProgress, SystemConfig


class Scanner:
    def __init__(self):
        self.smb = SMBManager()
        self._progress_lock = threading.Lock()

    def scan_all(self):
        """扫描入口：遍历目录，发现PDF后批量发送Celery任务到Redis队列"""
        # 加载设计编号缓存到内存
        from app.vision.models import design_cache_memory
        design_cache_memory.load_from_db()

        progress = ScanProgress.get()

        if progress['status'] == 'completed':
            ScanProgress.reset()
            progress = ScanProgress.get()

        # 读取排除目录配置
        exclude_dirs = self._get_exclude_dirs()
        print(f"[INFO] 排除目录: {exclude_dirs}")

        # 读取选中的目录列表
        selected_dirs = self._get_selected_dirs()
        if selected_dirs is not None:
            print(f"[INFO] 指定扫描目录: {len(selected_dirs)} 个")
        else:
            print(f"[INFO] 未指定目录，扫描全部")

        # 读取年份筛选
        year_filter = self._get_year_filter()
        if year_filter:
            print(f"[INFO] 年份筛选: 跳过修改时间早于 {year_filter} 年的目录")

        # 如果指定了目录，需要取全部来匹配
        page_size = 20
        if selected_dirs is not None:
            page_size = max(20, len(selected_dirs) + 10)

        dirs, total_dirs = self.smb.list_dirs(page=1, size=page_size)

        # 如果选中目录仍有遗漏，继续翻页
        if selected_dirs is not None:
            found = {d for d, m in dirs}
            missing = set(selected_dirs) - found
            page = 2
            while missing:
                more_dirs, _ = self.smb.list_dirs(page=page, size=page_size)
                if not more_dirs:
                    break
                dirs.extend(more_dirs)
                found.update(d for d, m in more_dirs)
                missing = set(selected_dirs) - found
                page += 1

        # 统计跳过前的总数
        before_filter = len(dirs)

        # 过滤排除目录
        dirs = [(d, m) for d, m in dirs if d not in exclude_dirs]

        # 过滤只保留选中的目录
        if selected_dirs is not None:
            selected_set = set(selected_dirs)
            dirs = [(d, m) for d, m in dirs if d in selected_set]

        # 年份筛选：跳过修改时间早于指定年份的目录
        if year_filter:
            from datetime import datetime
            year_start = datetime(year_filter, 1, 1).timestamp()
            dirs = [(d, m) for d, m in dirs if m >= year_start]

        skipped_dirs = before_filter - len(dirs)
        print(f"[INFO] 过滤后目录数: {len(dirs)}, 跳过: {skipped_dirs}")

        ScanProgress.update(
            status='running',
            total_dirs=total_dirs,
            skipped_dirs=skipped_dirs,
            total_files=0,
            started_at='NOW()',
        )

        start_dir_idx = progress.get('dir_index', 0)
        scanned = progress.get('scanned_files', 0)
        matched = progress.get('matched_files', 0)

        for dir_idx in range(start_dir_idx, len(dirs)):
            # 检查是否被重置
            cur = ScanProgress.get()
            if cur['status'] == 'idle':
                print(f"[INFO] 扫描已被重置，退出")
                return {'status': 'cancelled'}

            dirname, _ = dirs[dir_idx]

            # 跳过已完成的项目
            from app.vision.models import ScannedDirectory
            if ScannedDirectory.is_completed(dirname):
                print(f"[SKIP] 项目 {dirname} 已完成，跳过")
                continue

            # 标记项目为扫描中
            ScannedDirectory.create_or_update(
                dirname,
                status='scanning',
                started_at='NOW()',
            )

            # 清空该目录的临时文件
            from app.db import execute
            execute("DELETE FROM temp_files WHERE directory = %s", (dirname,))
            print(f"[INFO] 已清空项目 {dirname} 的临时文件")

            # === 发现PDF → 批量发送Celery任务 ===
            result = self._scan_and_dispatch(dirname)

            if result.get('status') == 'no_pdfs':
                print(f"[INFO] 项目 {dirname} 没有PDF文件")
                ScannedDirectory.create_or_update(
                    dirname,
                    status='pending',
                    total_files=0,
                )
                continue

            if result['status'] == 'paused':
                with self._progress_lock:
                    ScanProgress.update(
                        current_dir=dirname,
                        dir_index=dir_idx,
                    )
                return {'status': 'paused'}

            scanned += result.get('scanned', 0)
            matched += result.get('matched', 0)

            # 项目扫描完成，更新统计
            ScannedDirectory.create_or_update(
                dirname,
                status='pending',
                total_files=result.get('total_files', 0),
                scanned_files=scanned,
                matched_files=matched,
            )

        ScanProgress.update(
            status='completed',
            completed_at='NOW()',
        )

        return {'status': 'completed', 'scanned': scanned, 'matched': matched}

    def _scan_and_dispatch(self, dirname):
        """扫描目录中的PDF文件，批量发送Celery任务
        策略：和原来一样，不等扫描结束，凑够 阈值（线程数×3）就发
        每个 PDF = 1 个独立 Celery 任务，由 16 个 prefork worker 并行消费
        """
        num_threads = int(SystemConfig.get('scan_threads', '10'))
        num_threads = max(1, num_threads)
        threshold = num_threads * 3

        mount_path = self.smb._find_mount_path_for_dir(dirname)
        if not mount_path:
            return {'status': 'no_pdfs', 'scanned': 0, 'matched': 0, 'total_files': 0}

        dir_path = os.path.join(mount_path, dirname)
        real_dir = os.path.realpath(dir_path)
        real_mount = os.path.realpath(mount_path)

        if not real_dir.startswith(real_mount):
            return {'status': 'no_pdfs', 'scanned': 0, 'matched': 0, 'total_files': 0}

        # 先重置本目录的进度计数器
        ScanProgress.update(scanned_files=0, matched_files=0)

        from app.tasks import process_pdf_task

        batch = []
        count = 0
        dispatched = 0
        results = []

        for root, dirs, files in os.walk(real_dir):
            cur = ScanProgress.get()
            if cur['status'] in ('idle', 'paused'):
                print(f"[INFO] 目录 {dirname} 扫描中断，状态={cur['status']}")
                # 撤销已派发的任务
                self._wait_for_tasks(results, dirname)
                return {'status': 'paused', 'scanned': 0, 'matched': 0, 'total_files': count}

            base_depth = real_dir.rstrip(os.sep).count(os.sep)
            current_depth = root.rstrip(os.sep).count(os.sep)
            if current_depth - base_depth >= 256:
                del dirs[:]
                continue

            for name in sorted(files):
                if name.lower().endswith('.pdf'):
                    full = os.path.join(root, name)
                    rel_path = os.path.relpath(full, mount_path)
                    try:
                        size = os.path.getsize(full)
                    except OSError:
                        size = 0

                    batch.append({
                        'name': name,
                        'size': size,
                        'path': rel_path,
                    })
                    count += 1

                    # 凑够阈值 → 批量发送
                    if len(batch) >= threshold:
                        dispatched += self._dispatch_batch(batch, dirname, process_pdf_task, results)
                        batch = []

                    # 更新发现进度
                    if count % 50 == 0:
                        ScanProgress.update(
                            current_dir=dirname,
                            current_file=f'已发现 {count} 个PDF...',
                            total_files=count,
                        )

        # 发送剩余的
        if batch:
            dispatched += self._dispatch_batch(batch, dirname, process_pdf_task, results)

        ScanProgress.update(total_files=count)
        print(f"[INFO] 项目 {dirname} 共发现 {count} 个PDF，已派发 {dispatched} 个Celery任务")

        if count == 0:
            return {'status': 'no_pdfs', 'scanned': 0, 'matched': 0, 'total_files': 0}

        # 等待所有任务完成
        self._wait_for_tasks(results, dirname)

        # 从DB读取最终计数
        progress = ScanProgress.get()
        final_scanned = progress.get('scanned_files', 0)
        final_matched = progress.get('matched_files', 0)

        print(f"[INFO] 项目 {dirname} 完成: 发现={count}, 扫描={final_scanned}, 匹配={final_matched}")
        return {'status': 'completed', 'scanned': final_scanned, 'matched': final_matched, 'total_files': count}

    def _dispatch_batch(self, batch, dirname, task_func, results):
        """批量发送Celery任务到Redis队列"""
        for pdf in batch:
            r = task_func.delay(pdf, dirname)
            results.append(r.id)
        return len(batch)

    def _wait_for_tasks(self, task_ids, dirname):
        """通过DB计数器轮询等待所有Celery任务完成
        不逐个检查AsyncResult（上万任务太慢），而是看DB的scanned_files是否追平total_files
        """
        import time

        total = len(task_ids)
        if total == 0:
            return

        print(f"[INFO] 等待 {total} 个任务完成...")

        while True:
            cur = ScanProgress.get()
            if cur['status'] == 'idle':
                print(f"[INFO] 检测到重置，撤销剩余任务")
                from app import celery
                for tid in task_ids:
                    celery.control.revoke(tid, terminate=True)
                return

            scanned = cur.get('scanned_files', 0) or 0
            matched = cur.get('matched_files', 0) or 0
            total_files = cur.get('total_files', 0) or 0

            ScanProgress.update(current_file=f'{dirname} 进度 {scanned}/{total_files}')

            # 检查是否所有文件都处理完了
            if total_files > 0 and scanned >= total_files:
                print(f"[INFO] 所有任务完成: scanned={scanned}, matched={matched}")
                break

            # 超时保护：最多等待 2 小时
            # 通过检查DB更新时间判断是否还有活动
            time.sleep(2)

    def _get_exclude_dirs(self):
        """读取排除目录配置，逗号分隔"""
        exclude_str = SystemConfig.get('scan_exclude_dirs', '')
        if not exclude_str:
            return set()
        return set(d.strip() for d in exclude_str.split(',') if d.strip())

    def _get_selected_dirs(self):
        """读取选中的目录列表，返回 None 表示全部"""
        import json
        selected_json = SystemConfig.get('scan_selected_dirs', '')
        if not selected_json:
            return None
        try:
            dirs = json.loads(selected_json)
            return dirs if dirs else None
        except (json.JSONDecodeError, TypeError):
            return None

    def _get_year_filter(self):
        """读取年份筛选配置，返回 None 或整数年份"""
        year_str = SystemConfig.get('scan_year_filter', '')
        if not year_str:
            return None
        try:
            year = int(year_str)
            return year if year >= 2000 else None
        except (ValueError, TypeError):
            return None

    def mark_project_completed(self, directory):
        """标记项目完成并清理临时文件"""
        from app.vision.models import ScannedDirectory
        # 1. 标记项目完成
        ScannedDirectory.mark_completed(directory)

        # 2. 删除该项目的临时文件
        from app.db import execute
        execute("DELETE FROM temp_files WHERE directory = %s", (directory,))

        return {'success': True, 'message': f'项目 {directory} 已完成，临时文件已清理'}
