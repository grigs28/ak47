# PDF 扫描工作流重构设计规格书

> 日期：2026-04-23
> 状态：待审批

---

## 1. 项目概述

在 `/opt/yz/ak47` 重构 PDF 扫描工作流，核心变化：

- **提取工程信息**：从 PDF 第1页提取 建设单位、工程名称、设计编号、图名、图号、图别
- **设计编号为查找依据**：同一设计编号下的图纸共享工程信息
- **临时库 → 正式库**：先 qwen-3 图片识别判断是否为"说明"类文档，是则入临时库；再 OCR 找【】，有则入正式库
- **缓存机制**：已见过的设计编号直接 OCR，跳过 qwen-3 判断
- **结果保存**：正式库含 PDF 路径、MD 内容、JSON 提取结果

---

## 2. 架构设计

### 2.1 新模块（app/vision/）

```
app/vision/
├── __init__.py
├── extractor.py      # PDF 转图片 + 裁剪 + qwen-3 提取6字段
├── classifier.py     # qwen-3 判断是否为"说明"文档
├── ocr_client.py     # PaddleOCR 调用（从 app/ocr.py 迁移）
└── models.py         # Vision 相关数据模型
```

### 2.2 数据流

```
发现 PDF
    │
    ▼
┌─────────────────┐
│ 1. PDF转图片    │──▶ pdftoppm 第1页
│ 2. 裁剪区域     │──▶ 竖版底部20% / 横版右侧20%
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ 3. qwen-3提取   │──▶ 6字段JSON
│    6字段        │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ 4. 保存临时库   │──▶ temp_files 表
│    (设计编号KEY)│
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ 5. qwen-3判断   │──▶ 是否为"说明"文档
│    是否说明     │
└────────┬────────┘
    ┌────┴────┐
    ▼         ▼
  是说明    不是说明
    │         │
    ▼         ▼
┌────────┐  ┌────────┐
│6. OCR  │  │ 标记完成│
│  找【】 │  │ 跳过    │
└───┬────┘  └────────┘
    │
┌───┴───┐
▼       ▼
有【】  无【】
│       │
▼       ▼
┌────────────┐  ┌────────┐
│7. 保存正式库│  │ 标记完成│
│  pdf+md+json│  │        │
└────────────┘  └────────┘
```

---

## 3. 数据库设计

### 3.1 临时库表（temp_files）

```sql
CREATE TABLE IF NOT EXISTS temp_files (
    id              SERIAL PRIMARY KEY,
    file_path       VARCHAR(1000) NOT NULL,
    directory       VARCHAR(500) NOT NULL,
    filename        VARCHAR(500) NOT NULL,
    file_size       BIGINT,
    
    -- 提取的6字段
    建设单位        VARCHAR(500),
    工程名称        VARCHAR(1000),
    设计编号        VARCHAR(100) NOT NULL,
    图名            VARCHAR(500),
    图号            VARCHAR(50),
    图别            VARCHAR(50),
    
    -- 处理状态
    is_instruction  BOOLEAN DEFAULT NULL,  -- qwen-3判断是否说明文档
    status          VARCHAR(20) DEFAULT 'pending',  -- pending / instruction / not_instruction / completed
    
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_temp_files_design_number ON temp_files(设计编号);
CREATE INDEX idx_temp_files_status ON temp_files(status);
```

### 3.2 正式库表（scanned_files）扩展

```sql
-- 在现有 scanned_files 表基础上增加字段
ALTER TABLE scanned_files ADD COLUMN IF NOT EXISTS 建设单位 VARCHAR(500);
ALTER TABLE scanned_files ADD COLUMN IF NOT EXISTS 工程名称 VARCHAR(1000);
ALTER TABLE scanned_files ADD COLUMN IF NOT EXISTS 设计编号 VARCHAR(100);
ALTER TABLE scanned_files ADD COLUMN IF NOT EXISTS 图名 VARCHAR(500);
ALTER TABLE scanned_files ADD COLUMN IF NOT EXISTS 图号 VARCHAR(50);
ALTER TABLE scanned_files ADD COLUMN IF NOT EXISTS 图别 VARCHAR(50);
ALTER TABLE scanned_files ADD COLUMN IF NOT EXISTS json_result JSONB;
ALTER TABLE scanned_files ADD COLUMN IF NOT EXISTS is_instruction BOOLEAN DEFAULT FALSE;

CREATE INDEX idx_scanned_files_design_number ON scanned_files(设计编号);
```

### 3.3 设计编号缓存表（design_cache）

```sql
CREATE TABLE IF NOT EXISTS design_cache (
    设计编号        VARCHAR(100) PRIMARY KEY,
    建设单位        VARCHAR(500),
    工程名称        VARCHAR(1000),
    has_instruction BOOLEAN DEFAULT FALSE,  -- 是否已有说明文档
    instruction_count INTEGER DEFAULT 0,     -- 说明文档数量
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

---

## 4. 扫描流程设计

### 4.1 单文件处理流程

```python
def process_pdf(file_path):
    # 1. 提取6字段
    info = extract_info_from_pdf(file_path)
    design_number = info['设计编号']
    
    # 2. 保存临时库
    temp_id = save_to_temp(file_path, info)
    
    # 3. 检查缓存
    cache = get_design_cache(design_number)
    if cache and cache['has_instruction']:
        # 已有说明文档，直接OCR
        result = ocr_and_find_brackets(file_path)
        if result['has_brackets']:
            save_to_formal(file_path, info, result)
        mark_completed(temp_id)
        return
    
    # 4. qwen-3判断是否为说明文档
    is_instruction = classify_instruction(file_path)
    update_temp_status(temp_id, is_instruction)
    
    if is_instruction:
        # 5. OCR找【】
        result = ocr_and_find_brackets(file_path)
        if result['has_brackets']:
            save_to_formal(file_path, info, result)
        # 更新缓存
        update_design_cache(design_number, has_instruction=True)
    
    mark_completed(temp_id)
```

### 4.2 提取6字段 Prompt

```
请从这张建筑图纸图片中提取以下字段：
1. 建设单位
2. 工程名称（图名和图号之间的文字，可能多行）
3. 设计编号（必须包含至少一个"-"）
4. 图名
5. 图号
6. 图别

按JSON格式返回：
{"建设单位": "", "工程名称": "", "设计编号": "", "图名": "", "图号": "", "图别": ""}
找不到的字段填null。只返回JSON，不要其他内容。
```

### 4.3 判断说明文档 Prompt

```
请判断以下文档是否为"建筑设计说明"或"设计说明"类文档。

文档内容：
{content}

按JSON格式返回：
{"is_instruction": true/false, "confidence": 0.0-1.0, "reason": "判断理由"}
只返回JSON。
```

---

## 5. API 设计

### 5.1 新增 API

```
GET    /api/temp-files              临时库文件列表
GET    /api/temp-files/<id>         临时库文件详情
POST   /api/temp-files/<id>/classify 手动触发分类
GET    /api/design-cache            设计编号缓存列表
GET    /api/design-cache/<number>   指定设计编号详情
GET    /api/formal-files            正式库文件列表（含6字段筛选）
```

### 5.2 修改现有 API

```
GET    /api/files                   增加 design_number 筛选参数
GET    /api/files/<id>              返回增加6字段和json_result
```

---

## 6. UI 设计

### 6.1 新增页面

| 页面 | 路径 | 说明 |
|------|------|------|
| 临时库 | `/temp-files` | 待处理的PDF列表，显示6字段 |
| 设计编号 | `/design-cache` | 已识别的设计编号列表 |

### 6.2 仪表盘修改

- 增加"临时库数量"统计卡片
- 增加"已识别设计编号"统计卡片
- 扫描进度显示当前设计编号

---

## 7. 错误处理

| 场景 | 处理策略 |
|------|----------|
| 设计编号提取失败（无"-"） | 标记为失败，跳过，记录日志 |
| qwen-3 提取超时 | 重试2次，仍失败则标记为待人工处理 |
| OCR 超时 | 重试3次，标记 ocr_status=failed |
| 缓存不一致 | 定时任务清理过期缓存（7天） |

---

## 8. 规格自检

### 8.1 Placeholder 检查
- [x] 无 TBD/TODO
- [x] 所有表结构完整
- [x] API 路径完整

### 8.2 内部一致性
- [x] 临时库 → 正式库流程一致
- [x] 设计编号作为KEY贯穿始终

### 8.3 歧义消除
- "说明文档"：包含"设计说明"、"建筑设计说明"等字样的文档
- "正式库"：含【】且通过OCR确认的文档
- "标记完成"：无论是否入正式库，都标记为已处理

---

*本规格书经审批后，将基于此创建详细实现计划。*
