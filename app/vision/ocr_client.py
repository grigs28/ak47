import re
from app.ocr import OCRClient as BaseOCRClient


class VisionOCRClient(BaseOCRClient):
    """扩展 OCRClient，增加找【】功能"""

    def has_brackets(self, content):
        """检测内容是否含【】"""
        return bool(re.search(r'【[^】]+】', content))

    def find_brackets(self, content):
        """找出所有【】内容"""
        return re.findall(r'【([^】]+)】', content)

    def process_and_check(self, file_path):
        """处理文件并检查是否含【】"""
        task_id, md_content = self.process_file(file_path)
        has_brackets = self.has_brackets(md_content)
        return {
            'task_id': task_id,
            'md_content': md_content,
            'has_brackets': has_brackets,
            'brackets': self.find_brackets(md_content) if has_brackets else [],
        }
