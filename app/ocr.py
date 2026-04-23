import requests
import time
from app.models import SystemConfig


class OCRClient:
    def __init__(self):
        self.base_url = SystemConfig.get('paddleocr_base_url', 'http://192.168.0.19:5553')
        self.api_key = SystemConfig.get('paddleocr_api_key', '')

    def _headers(self):
        return {'X-API-Key': self.api_key}

    def submit_task(self, file_path, output_formats=None):
        """提交 OCR 任务，返回 task_id"""
        if output_formats is None:
            output_formats = ['markdown']

        url = f"{self.base_url}/api/v1/tasks"

        with open(file_path, 'rb') as f:
            files = {'file': f}
            data = {
                'task_type': 'ocr',
                'output_formats': str(output_formats),
            }
            resp = requests.post(url, headers=self._headers(), files=files, data=data, timeout=60)

        resp.raise_for_status()
        return resp.json()['task_id']

    def get_task(self, task_id):
        """查询任务状态"""
        url = f"{self.base_url}/api/v1/tasks/{task_id}"
        resp = requests.get(url, headers=self._headers(), timeout=30)
        resp.raise_for_status()
        return resp.json()

    def wait_for_completion(self, task_id, poll_interval=3, max_retries=600):
        """轮询等待任务完成"""
        for _ in range(max_retries):
            data = self.get_task(task_id)
            task = data.get('task', {})
            status = task.get('status')

            if status == 'completed':
                return data
            elif status == 'failed':
                raise RuntimeError(f"OCR task failed: {task.get('error_message')}")

            time.sleep(poll_interval)

        raise TimeoutError(f"OCR task {task_id} did not complete within timeout")

    def get_result(self, task_id):
        """获取识别结果（Markdown 内容）"""
        data = self.wait_for_completion(task_id)
        return data.get('result', '')

    def process_file(self, file_path):
        """处理单个文件：提交 -> 等待 -> 返回结果"""
        task_id = self.submit_task(file_path, output_formats=['markdown'])
        result = self.get_result(task_id)
        return task_id, result
