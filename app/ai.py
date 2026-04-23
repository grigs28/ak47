import requests
import json
import re
from app.models import SystemConfig


class AIMatcher:
    def __init__(self):
        self.base_url = SystemConfig.get('qwen_base_url', 'http://192.168.0.18:5566/v1')
        self.api_key = SystemConfig.get('qwen_api_key', '')
        self.model = SystemConfig.get('qwen_model', 'qwen3')
        self.enabled = SystemConfig.get('ai_enabled', 'true').lower() == 'true'
        self.standard = SystemConfig.get('gbt_standard', 'GBT 50378-2019(2024年版)')

    def match(self, content):
        """判断内容是否与绿色建筑标准相关"""
        if not self.enabled:
            return {'matched': None, 'confidence': None, 'reason': 'AI disabled'}

        text = content[:3000]

        prompt = f"""请判断以下文档内容是否与绿色建筑评价标准 {self.standard} 相关。

文档内容（前 3000 字符）：
{text}

请按以下 JSON 格式返回，不要包含其他内容：
{{"matched": true/false, "confidence": 0.0-1.0, "reason": "判断理由"}}"""

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
                        {'role': 'system', 'content': '你是一个文档分类助手，请严格按 JSON 格式返回结果。'},
                        {'role': 'user', 'content': prompt},
                    ],
                    'temperature': 0.1,
                    'max_tokens': 500,
                },
                timeout=60,
            )
            resp.raise_for_status()
            data = resp.json()

            ai_text = data['choices'][0]['message']['content']

            json_match = re.search(r'\{[^}]+\}', ai_text)
            if json_match:
                result = json.loads(json_match.group())
                return {
                    'matched': result.get('matched'),
                    'confidence': result.get('confidence'),
                    'reason': result.get('reason', ''),
                }
            else:
                return {'matched': None, 'confidence': None, 'reason': 'JSON parse failed'}

        except Exception as e:
            return {'matched': None, 'confidence': None, 'reason': f'AI error: {str(e)}'}

    @staticmethod
    def has_brackets(content):
        """检测内容是否含【】"""
        return bool(re.search(r'【[^】]+】', content))
