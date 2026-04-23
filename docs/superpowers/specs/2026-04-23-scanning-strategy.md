# 扫描策略设计文档

> 日期：2026-04-23
> 状态：已确认，待实现

---

## 1. 目录结构

```
SMB根目录/
├── 28/          ← 项目28（大目录）
├── 30.3/        ← 项目30.3
├── 79/          ← 项目79
├── 101/         ← 项目101
├── 103/         ← 项目103
├── 归档文件/     ← 已完成项目
└── 06/          ← 待扫描项目
```

- 每个"大目录"是一个**独立项目集合**
- 项目之间**设计编号可能重复**，但视为不同项目

---

## 2. 核心扫描规则

### 2.1 扫描顺序

- **按 mtime 最新优先**
- 先扫最新修改的项目，再扫旧项目
- 已标记"已完成"的项目**永久跳过**

### 2.2 项目内处理逻辑

```
进入项目 28：
  1. 获取项目下所有 PDF
  2. 按设计编号分组
  3. 对每个设计编号组：
       a. 查全局缓存 design_cache
       b. 缓存未命中 → 正常流程（qwen-3 → OCR找【】→ 入正式库）
       c. 缓存命中且 has_instruction=True → 完全跳过
  4. 项目扫描完成
```

### 2.3 跨项目设计编号处理

| 场景 | 处理 |
|------|------|
| 设计编号首次出现 | 正常流程，OCR找【】，入正式库，标记缓存 |
| 设计编号在其他项目已标记 | **完全跳过**，不入库，不做OCR，删tmp |
| 设计编号在其他项目未标记 | 正常流程（可能当前项目找到【】）|

**关键原则：** 设计编号缓存是**全局共享**的，一旦某个设计编号在任何项目中被标记 `has_instruction=True`，后续所有项目中的该设计编号都跳过。

---

## 3. 数据库表设计

### 3.1 design_cache（已有，需扩展）

```sql
CREATE TABLE design_cache (
    设计编号        VARCHAR(100) PRIMARY KEY,
    建设单位        VARCHAR(500),
    工程名称        VARCHAR(1000),
    has_instruction BOOLEAN DEFAULT FALSE,
    instruction_count INTEGER DEFAULT 0,
    first_seen_directory VARCHAR(500),  -- 新增：首次发现的项目
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

### 3.2 scanned_directories（新增）

```sql
CREATE TABLE scanned_directories (
    directory       VARCHAR(500) PRIMARY KEY,
    status          VARCHAR(20) DEFAULT 'pending',  -- pending / scanning / completed
    total_files     INTEGER DEFAULT 0,
    scanned_files   INTEGER DEFAULT 0,
    matched_files   INTEGER DEFAULT 0,
    started_at      TIMESTAMP,
    completed_at    TIMESTAMP,
    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

---

## 4. 项目完成流程

```
1. 扫描项目全部 PDF
2. 等待确认正式库数据正确（人工或自动校验）
3. 标记项目状态为 "completed"
   - INSERT INTO scanned_directories (directory, status, completed_at)
4. 删除该项目全部临时文件
   - DELETE FROM temp_files WHERE directory = '项目名'
5. SMB文件源tab显示"已完成"标签
6. 以后不再扫描该项目
```

---

## 5. SMB文件源Tab展示

```
目录列表（按最新修改排序）
┌─────────────┬──────────┬────────┐
│ 目录名       │ 修改时间  │ 状态   │
├─────────────┼──────────┼────────┤
│ 28          │ 2026-04-23│ 已完成 │
│ 30.3        │ 2026-04-22│ 扫描中 │
│ 79          │ 2026-04-20│ 待扫描 │
│ 101         │ 2026-04-18│ 待扫描 │
│ ...         │ ...      │ ...    │
└─────────────┴──────────┴────────┘
```

- 已完成项目：绿色标签，不参与后续扫描
- 扫描中项目：黄色标签，显示进度
- 待扫描项目：灰色标签，按mtime排队

---

## 6. 临时文件生命周期

```
PDF处理流程：
  1. 提取信息 → 入 temp_files（状态: pending）
  2. qwen-3判断 → 更新 temp_files（状态: instruction / not_instruction）
  3. OCR找【】 → 入正式库 scanned_files
  4. 项目完成确认 → DELETE temp_files WHERE directory = '项目名'
```

**清理时机：** 项目标记"已完成"后，确认正式库无误再清理。

---

## 7. 性能估算

假设：
- 每个项目平均 10,000 个 PDF
- 单文件处理平均 10 秒
- 并发 5 个 worker

| 指标 | 数值 |
|------|------|
| 单项目时间 | 10,000 × 10s ÷ 5 = 5.5 小时 |
| 17 个项目 | 约 4 天（单轮）|
| 跨项目跳过率 | 取决于设计编号重复率 |

**优化点：**
- 设计编号重复率越高，跳过越多，整体越快
- 已完成项目不再扫描，后续轮次只扫新增项目

---

## 8. 待实现清单

- [ ] 扩展 design_cache 表，添加 first_seen_directory
- [ ] 创建 scanned_directories 表
- [ ] 改造 scan_all() 支持项目级扫描
- [ ] 实现跨项目设计编号跳过逻辑
- [ ] 实现项目完成标记和临时文件清理
- [ ] SMB文件源Tab显示项目状态
- [ ] 支持项目手动标记"已完成"
