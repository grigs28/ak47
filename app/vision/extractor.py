import json
import re
import requests
import pdfplumber
from app.models import SystemConfig
from app.vision.utils import image_to_base64, pdf_page_to_image, crop_image_region, get_crop_strategy


# 6 个图签字段
TITLE_FIELDS = ['建设单位', '工程名称', '设计编号', '图名', '图号', '图别']

# 说明关键词（去空格后匹配）
INSTRUCTION_KEYWORDS = ['设计说明', '总说明', '设计总说明', '施工图设计总说明']


class InfoExtractor:
    def __init__(self):
        self.base_url = SystemConfig.get('qwen_base_url', 'http://192.168.0.18:5566/v1')
        self.api_key = SystemConfig.get('qwen_api_key', '')
        self.model = SystemConfig.get('qwen_model', 'qwen-3')

    # ==================== 文本提取路径 ====================

    def _extract_from_text(self, pdf_path):
        """用 pdfplumber 直接提取文本，匹配图签字段。
        成功返回 dict，失败返回 None。
        """
        try:
            with pdfplumber.open(pdf_path) as pdf:
                if not pdf.pages:
                    return None
                page = pdf.pages[0]
                text = page.extract_text() or ''
        except Exception:
            return None

        if not text.strip():
            return None

        # 去空格、全角空格、换行
        compact = text.replace(' ', '').replace('\u3000', '')

        # 检查 6 字段标签是否齐全
        missing = [f for f in TITLE_FIELDS if f not in compact]
        if missing:
            return None

        # 提取字段值（在原始文本上做正则，保留空格以便分隔）
        result = {}
        # 建设单位
        result['建设单位'] = self._extract_field(text, '建设单位')
        # 工程名称
        result['工程名称'] = self._extract_field(text, '工程名称')
        # 设计编号
        result['设计编号'] = self._extract_field(text, '设计编号')
        # 图名
        result['图名'] = self._extract_field(text, '图名')
        # 图号
        result['图号'] = self._extract_field(text, '图号')
        # 图别
        result['图别'] = self._extract_field(text, '图别')

        # 判断是否说明：仅关键词匹配
        is_instruction = any(kw in compact for kw in INSTRUCTION_KEYWORDS)

        result['is_instruction'] = is_instruction

        # 来源标记
        result['source'] = 'text'

        return result

    def _extract_field(self, text, field_name):
        """从文本中提取字段值。
        pdfplumber 按行提取，左右布局的文字可能合并在同一行。
        同一行中可能有正文标题 + 图签字段，如 "五、设计标高： 建设单位: xxx"
        策略：找到"字段名："后，只取该字段冒号后的同行内容。
        """
        lines = text.split('\n')

        for i, line in enumerate(lines):
            compact_line = line.replace(' ', '').replace('\u3000', '')
            pattern = field_name + '[：:]'
            m = re.search(pattern, compact_line)
            if not m:
                continue

            # 字段冒号后的内容
            after = compact_line[m.end():].strip()

            # 截断其他图签字段
            for other in TITLE_FIELDS:
                if other == field_name:
                    continue
                cut = after.find(other)
                if cut > 0:
                    after = after[:cut].strip()

            if after:
                return after

            # 同行冒号后为空，取下面行
            value_parts = []
            for j in range(i + 1, min(i + 5, len(lines))):
                nc = lines[j].replace(' ', '').replace('\u3000', '')
                # 遇到其他图签字段，停止
                if any(re.search(f2 + '[：:]', nc) for f2 in TITLE_FIELDS if f2 != field_name):
                    break
                if any(sw in nc for sw in ['姓名', '签名', '设计阶段', '日期', '备注', '项目']):
                    break
                if nc:
                    # 跳过正文行（以中文标号开头，如"十三.防水工程""二、设计依据："）
                    if re.match(r'^[一二三四五六七八九十]+[、．.]', nc):
                        # 如果没有冒号，纯正文标题行，跳过
                        if '：' not in nc and ':' not in nc:
                            continue
                        # 有冒号，正文标题+混合内容
                        if re.match(r'^[一二三四五六七八九十]+[、．.][^：:]+[：:]$', nc):
                            continue
                        cleaned = re.sub(r'^[一二三四五六七八九十]+[、．.][^：:]+[：:]', '', nc)
                        if not cleaned or re.match(r'^[（(]', cleaned):
                            continue
                        value_parts.append(cleaned)
                        continue
                    value_parts.append(nc)
                elif value_parts:
                    break
            return ''.join(value_parts).strip() if value_parts else None

        return None

    # ==================== 视觉识别路径（兜底） ====================

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
            return json.loads(text)
        except json.JSONDecodeError:
            match = re.search(r'\{[\s\S]*?\}', text)
            if match:
                return json.loads(match.group())
            raise

    def _validate_design_number(self, design_number):
        """验证设计编号必须包含 '-' """
        if not design_number:
            return False
        return '-' in str(design_number)

    def _extract_from_vision(self, pdf_path, max_retries=2):
        """视觉识别兜底：转图片 → 裁图 → qwen-3"""
        image_path = pdf_page_to_image(pdf_path, page=1, dpi=200)
        strategies = get_crop_strategy(image_path)

        prompt = """请从这张建筑图纸图片中提取以下字段：
1. 建设单位
2. 工程名称
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
                    resp_text = self._call_vision(crop_path, prompt)
                    result = self._parse_json(resp_text)

                    design_number = result.get('设计编号') or result.get('design_number')
                    if self._validate_design_number(design_number):
                        return {
                            '建设单位': result.get('建设单位'),
                            '工程名称': result.get('工程名称'),
                            '设计编号': design_number,
                            '图名': result.get('图名'),
                            '图号': result.get('图号'),
                            '图别': result.get('图别'),
                            'is_instruction': False,
                            'source': 'vision',
                            'crop_region': region,
                        }
                    else:
                        last_error = f"设计编号 '{design_number}' 不含 '-'"
                        break
                except Exception as e:
                    last_error = str(e)
                    if attempt < max_retries:
                        continue
                    break

        raise RuntimeError(f"视觉识别提取失败: {last_error}")

    # ==================== 主入口 ====================

    # 文件大小阈值：>= 1024KB 直接走视觉
    VISION_SIZE_THRESHOLD = 1024 * 1024  # 1MB

    def extract(self, pdf_path, max_retries=2):
        """提取 6 字段：<1024K文本优先，>=1024K直接视觉"""
        import time
        import os
        filename = os.path.basename(pdf_path)
        t0 = time.time()

        file_size = os.path.getsize(pdf_path)

        # >= 1024KB 直接走视觉（大CAD矢量PDF文本提取太慢）
        if file_size >= self.VISION_SIZE_THRESHOLD:
            result = self._extract_from_vision(pdf_path, max_retries)
            elapsed = time.time() - t0
            fields_summary = '/'.join(
                (str(result.get(f, ''))[:15]) for f in TITLE_FIELDS
            )
            print(f"[提取] {filename} | 视觉路径({file_size//1024}KB) | {elapsed:.2f}s | {fields_summary}")
            return result

        # < 1024KB 先文本提取
        result = self._extract_from_text(pdf_path)
        if result:
            design_number = result.get('设计编号')
            elapsed = time.time() - t0
            fields_summary = '/'.join(
                (str(result.get(f, ''))[:15]) for f in TITLE_FIELDS
            )
            print(f"[提取] {filename} | 文本路径({file_size//1024}KB) | {elapsed:.2f}s | {fields_summary} | 说明={result.get('is_instruction')}")
            if self._validate_design_number(design_number):
                return result
            result['设计编号'] = design_number or 'unknown'
            return result

        # 文本提取失败，走视觉
        result = self._extract_from_vision(pdf_path, max_retries)
        elapsed = time.time() - t0
        fields_summary = '/'.join(
            (str(result.get(f, ''))[:15]) for f in TITLE_FIELDS
        )
        print(f"[提取] {filename} | 视觉兜底({file_size//1024}KB) | {elapsed:.2f}s | {fields_summary}")
        return result

    def extract_with_ocr_fallback(self, pdf_path, ocr_client):
        """兼容旧接口"""
        return self.extract(pdf_path)
