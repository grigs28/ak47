import json
import re
import requests
from app.models import SystemConfig
from app.vision.utils import image_to_base64, pdf_page_to_image, crop_image_region, get_crop_strategy


class InfoExtractor:
    def __init__(self):
        self.base_url = SystemConfig.get('qwen_base_url', 'http://192.168.0.18:5566/v1')
        self.api_key = SystemConfig.get('qwen_api_key', '')
        self.model = SystemConfig.get('qwen_model', 'qwen-3')

    def _call_vision(self, image_path, prompt):
        """调用 qwen-3 vision API"""
        base64_image = image_to_base64(image_path)

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
                'max_tokens': 500,
            },
            timeout=60,
        )
        resp.raise_for_status()
        data = resp.json()
        return data['choices'][0]['message']['content']

    def _parse_json(self, text):
        """从文本中提取 JSON"""
        try:
            # 尝试直接解析
            return json.loads(text)
        except json.JSONDecodeError:
            # 尝试从文本中提取 JSON 块
            match = re.search(r'\{[\s\S]*?\}', text)
            if match:
                return json.loads(match.group())
            raise

    def _validate_design_number(self, design_number):
        """验证设计编号必须包含 '-' """
        if not design_number:
            return False
        return '-' in str(design_number)

    def extract(self, pdf_path, max_retries=2):
        """从 PDF 提取 6 字段，带裁剪区域回退"""
        # 1. PDF 转图片
        image_path = pdf_page_to_image(pdf_path, page=1, dpi=200)

        # 2. 获取裁剪策略
        strategies = get_crop_strategy(image_path)

        prompt = """请从这张建筑图纸图片中提取以下字段：
1. 建设单位
2. 工程名称（图名和图号之间的文字，可能多行）
3. 设计编号（必须包含至少一个"-"）
4. 图名
5. 图号
6. 图别

按JSON格式返回：
{"建设单位": "", "工程名称": "", "设计编号": "", "图名": "", "图号": "", "图别": ""}
找不到的字段填null。只返回JSON，不要其他内容。"""

        last_error = None
        for region in strategies:
            crop_path = crop_image_region(image_path, region=region)

            for attempt in range(max_retries + 1):
                try:
                    text = self._call_vision(crop_path, prompt)
                    result = self._parse_json(text)

                    # 验证设计编号
                    design_number = result.get('设计编号') or result.get('design_number')
                    if self._validate_design_number(design_number):
                        return {
                            '建设单位': result.get('建设单位') or result.get('建设单位'),
                            '工程名称': result.get('工程名称') or result.get('工程名称'),
                            '设计编号': design_number,
                            '图名': result.get('图名') or result.get('图名'),
                            '图号': result.get('图号') or result.get('图号'),
                            '图别': result.get('图别') or result.get('图别'),
                            'crop_region': region,
                        }
                    else:
                        last_error = f"设计编号 '{design_number}' 不含 '-'，尝试下一区域"
                        break  # 换下一个裁剪区域

                except Exception as e:
                    last_error = str(e)
                    if attempt < max_retries:
                        continue
                    break  # 换下一个裁剪区域

        # 所有策略都失败
        raise RuntimeError(f"提取6字段失败: {last_error}")

    def extract_with_ocr_fallback(self, pdf_path, ocr_client):
        """提取6字段，qwen-3 失败时用 OCR 兜底"""
        try:
            return self.extract(pdf_path)
        except Exception as e:
            # OCR 兜底：对裁剪区域做 OCR，然后让 qwen-3 分析文本
            image_path = pdf_page_to_image(pdf_path, page=1, dpi=200)
            strategies = get_crop_strategy(image_path)

            for region in strategies:
                crop_path = crop_image_region(image_path, region=region)
                try:
                    # OCR 识别裁剪区域
                    md_content = ocr_client.process_file(crop_path)

                    # 用 qwen-3 文本模型分析 OCR 结果
                    prompt = f"""请从以下建筑图纸OCR识别结果中提取以下字段：
1. 建设单位
2. 工程名称
3. 设计编号（必须包含至少一个"-"）
4. 图名
5. 图号
6. 图别

OCR内容：
{md_content[:2000]}

按JSON格式返回：
{{"建设单位": "", "工程名称": "", "设计编号": "", "图名": "", "图号": "", "图别": ""}}
找不到的字段填null。只返回JSON，不要其他内容。"""

                    resp = requests.post(
                        f"{self.base_url}/chat/completions",
                        headers={
                            'Authorization': f'Bearer {self.api_key}',
                            'Content-Type': 'application/json',
                        },
                        json={
                            'model': self.model,
                            'messages': [
                                {'role': 'system', 'content': '你是一个文档信息提取助手，请严格按 JSON 格式返回结果。'},
                                {'role': 'user', 'content': prompt},
                            ],
                            'temperature': 0.1,
                            'max_tokens': 500,
                        },
                        timeout=60,
                    )
                    resp.raise_for_status()
                    data = resp.json()
                    text = data['choices'][0]['message']['content']
                    result = self._parse_json(text)

                    design_number = result.get('设计编号') or result.get('design_number')
                    if self._validate_design_number(design_number):
                        return {
                            '建设单位': result.get('建设单位'),
                            '工程名称': result.get('工程名称'),
                            '设计编号': design_number,
                            '图名': result.get('图名'),
                            '图号': result.get('图号'),
                            '图别': result.get('图别'),
                            'crop_region': region,
                            'fallback': 'ocr',
                        }
                except Exception:
                    continue

            raise RuntimeError(f"qwen-3 和 OCR 兜底均失败: {e}")
