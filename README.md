# AK47 - 建筑图纸PDF扫描系统

自动扫描 SMB 共享目录中的建筑 PDF 文件，提取图签字段，识别说明文档，OCR 生成 Markdown 内容。

## 技术栈

- Flask 3.0 + Celery 5.3 + Redis 7 + openGauss（PostgreSQL 兼容）
- pdfplumber（文本提取）+ qwen-3 视觉模型（大文件/图片PDF识别）
- ThreadPoolExecutor 多线程扫描（1-10线程可配）
- pdftoppm（PDF转图片）+ OCR 生成 Markdown

## Docker 部署

### 前置条件

- Docker + docker-compose
- openGauss/PostgreSQL 数据库（需提前建好 ak47 库）
- Redis（可用容器自带或外部已有）
- 需访问的 SMB 共享存储

### 配置

修改 `docker/docker-compose.yml` 中的环境变量：

```yaml
DB_HOST=192.168.0.98      # 数据库地址
DB_PORT=5432
DB_NAME=ak47
DB_USER=grigs
DB_PASSWORD=your_password
REDIS_URL=redis://127.0.0.1:6379/0
```

### 构建启动

```bash
cd docker
DOCKER_BUILDKIT=0 docker-compose up -d --build
```

### 访问

- Web 界面: http://服务器IP:5556
- 默认使用 host 网络模式，直接访问宿主机 Redis

### 初始化数据库

首次部署需要建表：
```bash
docker exec -it docker-web-1 python init_db.py
```

### 常用命令

```bash
# 查看日志
docker logs -f docker-web-1
docker logs -f docker-worker-1

# 重启
docker-compose restart

# 停止
docker-compose down

# 重新构建（代码更新后）
DOCKER_BUILDKIT=0 docker-compose up -d --build
```

## 本地开发

```bash
pip install -r requirements.txt

# 启动 Web
python run.py

# 启动 Celery Worker
DB_PASSWORD=xxx celery -A celery_worker worker -l info -c 1
```

## 扫描流程

```
PDF文件
  │
  ├─ 判断PDF类型（文件大小）
  │   ├─ < 1024KB → pdfplumber 文本提取
  │   └─ >= 1024KB → qwen-3 视觉识别（pdftoppm转图+AI提取）
  │
  ├─ 提取6字段（建设单位/工程名称/设计编号/图名/图号/图别）
  │
  ├─ 判断是否说明文档
  │   ├─ 文本路径：关键词匹配（设计说明/总说明等）
  │   └─ 视觉路径：分类器模型判断
  │
  ├─ 标准名称模糊匹配（逗号关键词，全部匹配）
  │   ├─ 不匹配 → 留临时库（人工审核）
  │   └─ 匹配 ↓
  │
  ├─ 设计编号缓存检查
  │   ├─ 已缓存 → OCR生成MD → 入正式库
  │   └─ 未缓存 → OCR查找【】标记
  │       ├─ 找到 → 标记编号 + OCR入正式库
  │       └─ 未找到 → 跳过
  │
  └─ 非说明文档 → 跳过
```

## Web 界面功能

- **扫描控制**：开始/暂停/恢复/重置扫描
- **目录浏览**：SMB 目录列表，支持勾选、年份筛选
- **仪表盘**：实时进度、扫描速度、发现说明数、预计剩余
- **文件管理**：正式库文件查看、MD 下载、PDF 预览
- **系统配置**：SMB 路径、扫描线程、标准名称、排除目录等

## 配置项

通过 Web 界面 系统配置 管理：

| 配置项 | 说明 |
|--------|------|
| smb_server | SMB 服务器地址 |
| smb_username / smb_password | SMB 凭据 |
| smb_shares | 共享路径列表（JSON） |
| scan_threads | 扫描线程数（1-10） |
| gbt_standard | 标准名称关键词（逗号分隔，如 gb,50378,2019） |
| scan_exclude_dirs | 排除目录（逗号分隔） |
| scan_year_filter | 年份筛选（跳过早于该年的目录） |
| qwen_base_url | qwen 视觉模型 API 地址 |
| ocr_base_url | OCR 服务地址 |
