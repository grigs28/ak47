# PDF 绿色建筑规范扫描器 — 设计规格书

> 日期：2026-04-23
> 状态：待审批

---

## 1. 项目概述

在 `/opt/yz/ak47` 构建一个 PDF 绿色建筑规范扫描系统，核心能力：

- 只读挂载 SMB 共享 `\\192.168.0.79\abb\FS\10\D$\tbmdata\data\ftpdata`
- 从最大数字编号子目录开始扫描所有 PDF 文件
- 使用 PaddleOCR-ui 将 PDF 转为 Markdown
- 检测内容中的 `【】` 符号，结合 qwen-3 AI 模糊匹配 GBT 50378-2019(2024年版) 绿色建筑标准
- 仅记录含 `【】` 的文件到 openGauss 数据库
- 提供 Web 界面：目录导航、PDF 预览、复选框人工确认、后台扫描状态监控
- 管理员认证接入 yz-login 系统
- 支持暂停/恢复，进度持久化到文件级别
- 所有配置通过 Web 界面管理
- Docker 部署支持（生产环境），本地开发环境直接使用

---

## 2. 架构设计

### 2.1 技术栈

| 组件 | 技术 | 版本/说明 |
|------|------|-----------|
| Web 框架 | Flask | 3.0.x |
| 任务队列 | Celery | 5.3.x |
| 消息代理 | Redis | 7.x |
| 数据库 | openGauss | 3.0.x (PostgreSQL 兼容) |
| OCR 服务 | PaddleOCR-ui | REST API |
| AI 服务 | qwen-3 | OpenAI 兼容 API |
| 前端 | Jinja2 + Bootstrap 5 | 同 yz-login 风格 |
| 部署 | Docker + Docker Compose | 生产环境 |

### 2.2 服务拓扑

```
┌─────────────────────────────────────────────────────────────┐
│                      Docker Network                          │
│                                                              │
│  ┌─────────────┐    ┌─────────────┐    ┌─────────────────┐  │
│  │   Flask     │───▶│    Redis    │    │   openGauss     │  │
│  │   (Web)     │◀───│  (Broker)   │    │   (Database)    │  │
│  │  port 5556  │    │  port 6379  │    │  port 5432      │  │
│  └──────┬──────┘    └──────┬──────┘    └─────────────────┘  │
│         │                  │                                 │
│         │    ┌─────────────┘                                 │
│         │    ▼                                               │
│         │  ┌─────────────┐                                   │
│         └──▶│   Celery    │                                   │
│            │   Worker    │                                   │
│            │  (Scanner)  │                                   │
│            └─────────────┘                                   │
│                                                              │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
                    ┌─────────────────┐
                    │  SMB (只读挂载)  │
                    │  \\192.168.0.79  │
                    └─────────────────┘
                              │
              ┌───────────────┼───────────────┐
              ▼               ▼               ▼
        ┌──────────┐   ┌──────────┐   ┌──────────┐
        │PaddleOCR │   │  qwen-3  │   │ yz-login │
        │  :5553   │   │  :5566   │   │  :5555   │
        └──────────┘   └──────────┘   └──────────┘
```

### 2.3 进程职责

- **Flask Web (5556)**：HTTP 服务、页面渲染、API 接口、配置管理、认证回调
- **Celery Worker**：后台扫描任务、OCR 调用、AI 匹配、数据库写入
- **Redis**：Celery 消息队列 + 任务状态缓存
- **openGauss**：持久化扫描进度、文件记录、配置、用户操作记录

---

## 3. 数据库设计

### 3.1 表结构

```sql
-- 扫描进度（单条记录，id=1）
CREATE TABLE IF NOT EXISTS scan_progress (
    id              SERIAL PRIMARY KEY,
    status          VARCHAR(20) NOT NULL DEFAULT 'idle',  -- idle / running / paused / completed
    current_dir     VARCHAR(500),                         -- 当前扫描目录
    current_file    VARCHAR(500),                         -- 当前扫描文件（含路径）
    dir_index       INTEGER DEFAULT 0,                    -- 当前目录在排序后列表中的索引
    file_index      INTEGER DEFAULT 0,                    -- 当前文件在目录中的索引
    total_dirs      INTEGER DEFAULT 0,                    -- 总目录数
    total_files     INTEGER DEFAULT 0,                    -- 总文件数
    scanned_files   INTEGER DEFAULT 0,                    -- 已扫描文件数
    matched_files   INTEGER DEFAULT 0,                    -- 匹配【】的文件数
    started_at      TIMESTAMP,
    paused_at       TIMESTAMP,
    completed_at    TIMESTAMP,
    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 扫描发现的文件（仅含【】的文件）
CREATE TABLE IF NOT EXISTS scanned_files (
    id              SERIAL PRIMARY KEY,
    file_path       VARCHAR(1000) NOT NULL,               -- 相对 SMB 根的路径
    directory       VARCHAR(500) NOT NULL,                -- 所属目录
    filename        VARCHAR(500) NOT NULL,
    file_size       BIGINT,
    has_brackets    BOOLEAN DEFAULT FALSE,                -- 是否含【】
    ai_matched      BOOLEAN DEFAULT NULL,                 -- AI 是否匹配 GBT 50378
    ai_confidence   FLOAT,                                -- AI 置信度
    ai_reason       TEXT,                                 -- AI 判断理由
    ocr_status      VARCHAR(20) DEFAULT 'pending',        -- pending / processing / done / failed
    ocr_task_id     INTEGER,                              -- PaddleOCR task_id
    md_content      TEXT,                                 -- OCR 后的 Markdown 内容
    page_count      INTEGER,                              -- PDF 页数
    selected        BOOLEAN DEFAULT FALSE,                -- 用户是否选中转 md
    converted       BOOLEAN DEFAULT FALSE,                -- 是否已转换下载
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    scanned_at      TIMESTAMP
);

-- 系统配置（键值对）
CREATE TABLE IF NOT EXISTS system_config (
    key             VARCHAR(100) PRIMARY KEY,
    value           TEXT,
    description     VARCHAR(500),
    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 用户操作日志
CREATE TABLE IF NOT EXISTS operation_logs (
    id              SERIAL PRIMARY KEY,
    user_id         INTEGER NOT NULL,
    username        VARCHAR(100),
    action          VARCHAR(50) NOT NULL,                 -- start_scan / pause_scan / select_file / convert_md / etc
    detail          JSONB,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

### 3.2 初始配置数据

```sql
INSERT INTO system_config (key, value, description) VALUES
('smb_server', '192.168.0.79', 'SMB 服务器地址'),
('smb_share', 'abb/FS/10/D$/tbmdata/data/ftpdata', 'SMB 共享路径'),
('smb_username', '', 'SMB 用户名'),
('smb_password', '', 'SMB 密码'),
('smb_mount_path', '/mnt/smb/ftpdata', 'SMB 容器内挂载点'),
('paddleocr_base_url', 'http://192.168.0.19:5553', 'PaddleOCR-ui 地址'),
('paddleocr_api_key', 'ak_e10b412d5cd68eeef303c3f561405dfb07d7e122123df8f97d0ecb30e5624d', 'PaddleOCR API Key'),
('qwen_base_url', 'http://192.168.0.18:5566/v1', 'qwen-3 API 地址'),
('qwen_api_key', '', 'qwen-3 API Key'),
('qwen_model', 'qwen3', 'qwen-3 模型名'),
('yz_login_url', 'http://192.168.0.19:5555', 'yz-login 地址'),
('scan_concurrency', '1', '并发扫描数'),
('ai_enabled', 'true', '是否启用 AI 辅助匹配'),
('gbt_standard', 'GBT 50378-2019(2024年版)', '绿色建筑标准名称');
```

---

## 4. 扫描流程设计

### 4.1 目录遍历策略

1. 读取 SMB 挂载根目录下所有子目录
2. 提取目录名中的数字部分，按数字从大到小排序
3. 依次进入每个目录，遍历其中所有 `.pdf` 文件（按文件名排序）
4. 进度持久化到 `scan_progress` 表（每次完成一个文件后更新）

### 4.2 单文件处理流程

```
发现 PDF 文件
    │
    ▼
┌─────────────────┐
│ 提交 PaddleOCR  │──▶ 异步任务，获取 task_id
│   转 Markdown   │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ 轮询 OCR 结果   │──▶ 每 3 秒查询，直到 completed/failed
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ 检测【】符号    │──▶ 正则匹配 /【[^】]+】/
└────────┬────────┘
         │
    ┌────┴────┐
    ▼         ▼
  含【】    不含【】
    │         │
    ▼         ▼
┌────────┐  ┌────────┐
│AI 匹配 │  │ 跳过   │
│(可选)   │  │ 不计入 │
└───┬────┘  └────────┘
    │
    ▼
┌─────────────────┐
│ 写入数据库      │──▶ scanned_files 表
│ (含 OCR 内容)   │
└─────────────────┘
```

### 4.3 AI 匹配 Prompt

```
请判断以下文档内容是否与绿色建筑评价标准 GBT 50378-2019(2024年版) 相关。

文档内容（前 3000 字符）：
{content}

请按以下 JSON 格式返回：
{
  "matched": true/false,
  "confidence": 0.0-1.0,
  "reason": "判断理由"
}
```

### 4.4 暂停/恢复机制

- **暂停**：Celery 任务检查 `scan_progress.status`，若为 `paused` 则休眠 5 秒后重试
- **恢复**：从 `scan_progress` 读取 `current_dir` + `dir_index` + `file_index`，继续扫描
- **浏览器关闭不影响**：进度在数据库，Celery Worker 独立运行

---

## 5. Web 界面设计

### 5.1 页面清单

| 页面 | 路径 | 说明 |
|------|------|------|
| 登录页 | `/login` | 跳转 yz-login OAuth |
| 仪表盘 | `/` | 扫描状态、统计、控制按钮 |
| 文件列表 | `/files` | 含【】的文件，可筛选、排序 |
| 文件详情 | `/files/<id>` | PDF 预览、OCR 内容、AI 判断 |
| 目录浏览 | `/browse` | SMB 目录树，点击跳转 |
| 系统配置 | `/settings` | 所有参数配置 |
| 操作日志 | `/logs` | 用户操作记录 |

### 5.2 仪表盘布局

```
┌─────────────────────────────────────────────┐
│  导航栏：仪表盘 | 文件列表 | 目录浏览 | 配置 │
├─────────────────────────────────────────────┤
│                                             │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐    │
│  │ 总目录   │ │ 已扫描   │ │ 匹配数   │    │
│  │   128    │ │  3,456   │ │   89     │    │
│  └──────────┘ └──────────┘ └──────────┘    │
│                                             │
│  ┌─────────────────────────────────────┐   │
│  │ 扫描进度：██████░░░░░░ 45%           │   │
│  │ 当前：/12/某项目/结构说明.pdf        │   │
│  └─────────────────────────────────────┘   │
│                                             │
│  [开始扫描] [暂停] [重置]                   │
│                                             │
│  最近匹配文件（5条）                        │
│  ┌─────────────────────────────────────┐   │
│  │ 文件名          │ 目录    │ AI匹配  │   │
│  │ 绿色建筑说明.pdf │ /128/   │  92%   │   │
│  └─────────────────────────────────────┘   │
│                                             │
└─────────────────────────────────────────────┘
```

### 5.3 文件列表页

- 表格展示：文件名、目录、大小、AI 匹配度、页数、选中状态
- 筛选：按目录、AI 匹配度范围、选中状态
- 批量操作：全选/取消、批量标记转 md
- 点击文件名进入详情页

### 5.4 文件详情页

- 左侧：PDF 预览（`<iframe>` 或 `<embed>` 加载 `/preview/<id>`）
- 右侧：OCR Markdown 内容（只读，带语法高亮）
- AI 判断卡片：匹配度、理由
- 操作：选中/取消选中、重新 OCR、重新 AI 匹配

---

## 6. 认证集成

### 6.1 yz-login 接入流程

```
用户访问 /login
    │
    ▼
重定向到 yz-login /auth/sso?app_id=ak47&redirect_uri=...
    │
    ▼
yz-login 验证后回调 /callback?ticket=xxx
    │
    ▼
验证 ticket，获取用户信息
    │
    ▼
检查 is_admin == 1，非管理员拒绝
    │
    ▼
写入 session，跳转仪表盘
```

### 6.2 保护机制

- 所有页面（除 `/login`、`/callback`）需登录
- 所有 API 需登录（`@login_required`）
- 仅管理员可访问（`@admin_required`）

---

## 7. API 设计

### 7.1 扫描控制

```
POST   /api/scan/start          开始扫描
POST   /api/scan/pause          暂停扫描
POST   /api/scan/resume         恢复扫描
POST   /api/scan/reset          重置进度
GET    /api/scan/status         获取扫描状态
```

### 7.2 文件管理

```
GET    /api/files               文件列表（分页、筛选）
GET    /api/files/<id>          文件详情
POST   /api/files/<id>/select   选中/取消选中
POST   /api/files/batch-select  批量选中
GET    /api/files/<id>/preview  PDF 预览（流式返回）
GET    /api/files/<id>/md       下载 Markdown
```

### 7.3 目录浏览

```
GET    /api/browse?path=...     目录内容（子目录+PDF 文件）
```

### 7.4 配置管理

```
GET    /api/config              获取所有配置
PUT    /api/config/<key>        更新单个配置
POST   /api/config/test-smb     测试 SMB 挂载
POST   /api/config/test-ocr     测试 PaddleOCR 连接
POST   /api/config/test-ai      测试 qwen-3 连接
```

---

## 8. Docker 部署

### 8.1 目录结构

```
/opt/yz/ak47/
├── docker/
│   ├── docker-compose.yml      # 生产编排
│   ├── Dockerfile              # Flask + Celery 镜像
│   ├── Dockerfile.worker       # (可选) 独立 Worker 镜像
│   └── nginx.conf              # 反向代理配置
├── app/
│   ├── __init__.py
│   ├── main.py                 # Flask app
│   ├── tasks.py                # Celery tasks
│   ├── models.py               # 数据库模型
│   ├── config.py               # 配置
│   ├── auth.py                 # 认证
│   ├── scan.py                 # 扫描逻辑
│   ├── ai.py                   # AI 匹配
│   ├── ocr.py                  # OCR 调用
│   ├── smb.py                  # SMB 操作
│   ├── utils.py                # 工具函数
│   ├── templates/              # Jinja2 模板
│   └── static/                 # CSS/JS
├── requirements.txt
├── celery_worker.py            # Celery 启动入口
├── run.py                      # Flask 启动入口
└── init_db.py                  # 数据库初始化
```

### 8.2 docker-compose.yml

```yaml
version: '3.8'

services:
  web:
    build: .
    ports:
      - "5556:5556"
    environment:
      - FLASK_ENV=production
      - REDIS_URL=redis://redis:6379/0
      - DB_HOST=192.168.0.98
      - DB_PORT=5432
      - DB_NAME=yz_relay
      - DB_USER=grigs
      - DB_PASSWORD=Slnwg123$
    cap_add:
      - SYS_ADMIN
      - DAC_READ_SEARCH
    security_opt:
      - apparmor:unconfined
    devices:
      - /dev/fuse:/dev/fuse
    depends_on:
      - redis
    command: gunicorn -b 0.0.0.0:5556 run:app

  worker:
    build: .
    environment:
      - REDIS_URL=redis://redis:6379/0
      - DB_HOST=192.168.0.98
      - DB_PORT=5432
      - DB_NAME=yz_relay
      - DB_USER=grigs
      - DB_PASSWORD=Slnwg123$
    cap_add:
      - SYS_ADMIN
      - DAC_READ_SEARCH
    security_opt:
      - apparmor:unconfined
    devices:
      - /dev/fuse:/dev/fuse
    depends_on:
      - redis
    command: celery -A celery_worker worker -l info -c 1

  redis:
    image: redis:7-alpine
    ports:
      - "6379:6379"
```

### 8.3 本地开发环境

不使用 Docker，直接运行：

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 确保 Redis 运行
redis-server

# 3. 初始化数据库
python init_db.py

# 4. 启动 Flask
python run.py

# 5. 启动 Celery Worker（另一个终端）
celery -A celery_worker worker -l info -c 1
```

---

## 9. 错误处理

| 场景 | 处理策略 |
|------|----------|
| SMB 挂载断开 | 扫描任务标记失败，记录错误，等待恢复后手动重试 |
| PaddleOCR 超时 | 重试 3 次，间隔 10 秒，仍失败则标记 `ocr_status=failed` |
| qwen-3 不可用 | 跳过 AI 匹配，`ai_matched=null`，保留 `【】` 检测结果 |
| 数据库连接失败 | Celery 任务自动重试（指数退避） |
| PDF 损坏 | 跳过，记录日志，继续下一个文件 |
| 磁盘空间不足 | 扫描暂停，Web 界面报警 |

---

## 10. 安全考虑

- SMB 只读挂载（`:ro`）
- 所有配置项敏感值（API Key、密码）加密存储
- yz-login 认证 + 管理员权限检查
- 文件路径校验（防止目录遍历攻击）
- PDF 预览使用流式读取，不缓存到本地磁盘

---

## 11. 扫描完成通知

扫描完成时（status 变为 `completed`），Web 界面通过以下方式提醒：

1. **仪表盘通知横幅**：页面顶部显示绿色成功提示"扫描完成！共发现 X 个匹配文件"
2. **浏览器通知**：请求 Notification API 权限，发送桌面通知
3. **最近匹配文件列表**：自动刷新显示最新匹配结果

通知内容：
- 总扫描文件数
- 匹配【】文件数
- AI 高置信度匹配数（confidence > 0.8）
- 跳转到文件列表的链接

---

## 12. 规格自检

---

## 12. 规格自检

### 12.1 Placeholder 检查
- [x] 无 TBD/TODO
- [x] 所有表结构完整
- [x] API 路径完整
- [x] 配置项完整

### 12.2 内部一致性
- [x] 数据库表名与 API 引用一致
- [x] 端口定义一致（Flask 5556, Redis 6379, OCR 5553, AI 5566, yz-login 5555）
- [x] 认证流程与 yz-login 能力匹配

### 12.3 范围检查
- 本项目聚焦：扫描 → OCR → AI 匹配 → 人工确认 → 转 md
- 不包含：PDF 编辑、非 PDF 文件处理、多用户并发操作、自动转 md（需人工确认）

### 12.4 歧义消除
- "最大数字编号子目录"：提取目录名中的连续数字，按数值降序排列
- "文件级别暂停"：记录到 `current_file` 字段，恢复时从该文件的下个文件继续
- "仅记录含【】"：`scanned_files` 表只插入含【】的文件，不含的跳过不记录

---

*本规格书经审批后，将基于此创建详细实现计划。*
