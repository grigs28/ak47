import json
import re
import requests
from app.models import SystemConfig
from app.vision.utils import image_to_base64


class InstructionClassifier:
    def __init__(self):
        self.base_url = SystemConfig.get('qwen_base_url', 'http://192.168.0.18:5566/v1')
        self.api_key = SystemConfig.get('qwen_api_key', '')
        self.model = SystemConfig.get('qwen_model', 'qwen-3')

    def classify(self, image_path, max_retries=2):
        """判断图片是否为说明文档，返回 (is_instruction, confidence)"""
        base64_image = image_to_base64(image_path)

        prompt = """请判断这张图片是否为"建筑设计说明"或"设计说明"类文档。

按JSON格式返回：
{"is_instruction": true/false, "confidence": 0.0-1.0, "reason": "判断理由"}
只返回JSON，不要其他内容。"""

        for attempt in range(max_retries + 1):
            try:
                resp = requests.post(
                    f"{self.base_url}/chat/completions",
                    headers={
                        'Authorization': f'Bearer {self.api_key}',
                        'Content-Type': 'application/json',
                    },
                    json={
                        'model': self.model,
                        'messages': [
                            {
                                'role': 'user',
                                'content': [
                                    {'type': 'text', 'text': prompt},
                                    {'type': 'image_url', 'image_url': {'url': f'data:image/png;base64,{base64_image}'}},
                                ]
                            }
                        ],
                        'temperature': 0.1,
                        'max_tokens': 200,
                    },
                    timeout=60,
                )
                resp.raise_for_status()
                data = resp.json()
                text = data['choices'][0]['message']['content']

                # 解析 JSON
                try:
                    result = json.loads(text)
                except json.JSONDecodeError:
                    match = re.search(r'\{[\s\S]*?\}', text)
                    if match:
                        result = json.loads(match.group())
                    else:
                        raise

                is_instruction = result.get('is_instruction', False)
                confidence = result.get('confidence', 0.5)

                # confidence > 0.6 才认为是说明
                return is_instruction and confidence > 0.6, confidence

            except Exception as e:
                if attempt < max_retries:
                    continue
                # 全部失败，保守返回 False
                return False, 0.0
