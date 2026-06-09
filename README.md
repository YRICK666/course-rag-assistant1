# 基于 RAG 的课程资料智能学习助手

本项目是一个本地运行的课程资料智能学习助手。系统按科目管理课程资料，将 PPT、PDF、Word、TXT 等文件解析为可检索知识库，通过 **FastAPI 后端 + React 前端** 完成资料管理、建库、智能问答、复习与自测。

采用 RAG（Retrieval-Augmented Generation）流程：先从课程资料库中检索相关片段，再使用 DeepSeek 或 OpenAI-compatible 模型生成答案，并保留来源引用。

> 旧版 Streamlit 界面仍保留在 `src/app.py`，但不再是当前主入口。

---

## 核心能力

- **多科目资料管理**：不同课程拥有独立的资料目录和知识库
- **文件格式支持**：`.ppt` / `.pptx` / `.pdf` / `.docx` / `.txt`；`.ppt` 会自动通过 LibreOffice 转换为 `.pptx`
- **灵活建库**：全部资料、单个资料、多选资料、按系统识别分组建库，支持增量更新和重建
- **智能问答**：基于检索结果生成答案，保留来源编号
- **Evidence Ledger（来源依据）**：展示每段答案对应的资料原文，支持展开/收起，可点击进入页面预览
- **Source Preview（页面预览）**：直接查看 PDF 页或 PPT 幻灯片原文
- **资料概览**：对当前资料范围生成内容主线、核心概念和复习价值
- **复习提纲**：生成考前 checklist、重点和易混点
- **Longform 资料整理**：深度分析、学习笔记、综合报告等多种长文输出
- **自测题生成**：独立接口，支持两种组卷模式（详见下文）
- **E 编号来源预览**：点击 `E1`、`[E1]`、`【E1】` 等编号直接跳转到对应页面预览或来源卡片
- **Word / PDF 导出**：将自测题或文档内容导出为可下载文件
- **AI 设置**：支持 DeepSeek 和 OpenAI-compatible 供应商，快速/深度推理模式切换
- **问答历史记录**：每次问答、自测题、Longform 生成自动保存，支持查看和删除
- **AI 临时关闭**：关闭后仍可使用 RAG 检索、来源查看和页面预览

---

## 技术架构

```
React 18 + TypeScript + Vite   ←  前端
        ↓  HTTP (127.0.0.1:8000)
FastAPI (Python)                ←  后端路由
        ↓
后端服务层（qa、self-test、longform、overview、study-guide、export）
        ↓
ChromaDB（向量检索）           ←  知识库
BAAI/bge-small-zh-v1.5         ←  Embedding
        ↓
DeepSeek / OpenAI-compatible   ←  LLM 生成
```

各服务链路独立：
- **智能问答**：检索 → LLM 回答
- **自测题**：检索 → 页面分组 → 证据分配 → 蓝图 → 正式命题（single_page 或 fusion）
- **资料整理**：检索 → 分组 → 逐组摘要 → 合成长文
- **概览/提纲**：检索 → LLM 生成

---

## 项目目录

```text
课程资料RAG查询/
├── AGENTS.md                       # AI 助手工作规则
├── CLAUDE.md                       # 项目层级规则
├── README.md
├── requirements.txt
├── .gitignore
├── docs/                           # 实施报告与交接文档
├── subjects/                       # 多科目资料库（本地数据，不提交）
│   └── 科目名称/
│       ├── materials/              # 原始课程资料
│       └── outputs/
│           ├── chroma_db/          # Chroma 向量索引
│           ├── extracted_text/     # 解析后的文本切块
│           ├── page_preview_cache/ # PDF/PPT 页面预览缓存
│           └── archived_original_ppt/
├── src/                            # Python 核心库
│   ├── app.py                      # 旧版 Streamlit 入口（保留，非当前入口）
│   ├── llm_deepseek.py             # DeepSeek / OpenAI API 调用（含自动重试）
│   ├── ingest.py                   # 资料解析、切分、Embedding、Chroma 建库
│   ├── retriever.py                # 向量检索与来源处理
│   ├── overview.py                 # 资料概览 / 复习提纲生成
│   ├── ai_settings.py              # AI 配置管理
│   ├── services/
│   │   ├── qa_service.py           # 问答服务
│   │   ├── index_service.py        # 建库服务
│   │   ├── learning_service.py     # 概览/提纲服务
│   │   └── scope_service.py
│   └── ...（material_manager、ppt_converter、subject_store 等）
├── backend/
│   └── app/
│       ├── main.py                 # FastAPI 入口（所有路由）
│       ├── services/
│       │   ├── self_test_service.py # 自测题页面级组卷（single_page / fusion）
│       │   ├── longform_service.py  # 长文资料整理
│       │   └── material_service.py  # 资料上传、删除、重命名
│       ├── db.py                   # SQLAlchemy 引擎和初始化
│       ├── models.py               # QAHistory ORM 模型
│       └── qa_history.py           # 问答历史 CRUD
├── frontend/
│   ├── package.json
│   ├── vite.config.ts
│   └── src/
│       ├── App.tsx                 # React 主界面
│       ├── styles.css
│       ├── api.ts                  # 后端 API 调用
│       └── types.ts                # TypeScript 类型定义
└── config/
    └── ai_settings.local.json      # 本地 AI 配置（不提交）
```

---

## 环境准备

### Python

Python 3.10 或 3.11 建议。

```powershell
cd G:\AI-Workstation\课程资料RAG查询
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

首次建库会自动下载 Embedding 模型 `BAAI/bge-small-zh-v1.5`。

### Node.js

建议 Node.js 18+（package.json 未指定最低版本，但本项目的依赖工具链通常需要 Node.js 18 或更高版本）。然后在 frontend 目录安装依赖：

```powershell
cd G:\AI-Workstation\课程资料RAG查询\frontend
npm install
```

### LibreOffice

LibreOffice 用于：
- `.ppt` → `.pptx` 自动转换
- **PDF 导出**：通过 `--headless` 将临时 DOCX 转为 PDF

安装后测试：

```powershell
soffice --version
```

Windows 常见安装路径：`C:\Program Files\LibreOffice\program\soffice.exe`

### DeepSeek / API 配置

复制环境变量模板：

```powershell
Copy-Item .env.example .env
```

编辑 `.env`：

```env
DEEPSEEK_API_KEY=your_key_here
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-v4-flash
EMBEDDING_MODEL=BAAI/bge-small-zh-v1.5
```

未配置 API Key 时，系统仍可进行本地检索、查看来源和页面预览。

---

## 启动方式

### 后端（开发模式，代码修改后自动重载）

```powershell
$Root = "G:\AI-Workstation\课程资料RAG查询"

# 停止旧的后端进程
$Listener = Get-NetTCPConnection `
    -LocalPort 8000 `
    -State Listen `
    -ErrorAction SilentlyContinue

if ($Listener) {
    $Listener.OwningProcess |
        Sort-Object -Unique |
        ForEach-Object {
            Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue
        }
    Start-Sleep -Seconds 1
}

Set-Location -LiteralPath $Root

& ".\.venv\Scripts\python.exe" `
    -m uvicorn backend.app.main:app `
    --reload `
    --reload-dir backend `
    --reload-dir src `
    --host 127.0.0.1 `
    --port 8000
```

### 前端

```powershell
Set-Location "G:\AI-Workstation\课程资料RAG查询\frontend"
npm run dev
```

前端默认运行在 `http://127.0.0.1:5173`。

### 后端稳定模式（展示/答辩，不自动重载）

```powershell
Set-Location "G:\AI-Workstation\课程资料RAG查询"
& ".\.venv\Scripts\python.exe" `
    -m uvicorn backend.app.main:app `
    --host 127.0.0.1 `
    --port 8000
```

### 重要说明

1. 后端和前端**需要分别启动**，两个终端窗口。
2. 修改后端代码或新增路由后，**必须重启正确项目目录下的后端**。若 8000 端口仍由旧进程占用，会出现代码中有路由但接口返回 404 的情况。
3. 可通过 `http://127.0.0.1:8000/openapi.json` 确认实际加载的路由。
4. 启动日志显示 `Will watch for changes in these directories: ['backend', 'src']` 表示只监控代码目录，不监控课程资料和 ChromaDB 数据。
5. 启动命令直接调用项目 `.venv` 中的 `python.exe`，而非先 `Activate.ps1` 再 `python`，可以避免系统中多个 Python 版本或虚拟环境带来的混淆。两种方式都可正常使用，但直接指定路径更稳妥。

---

## 推荐使用流程

1. **创建/选择科目**：左侧栏点击「+ 添加科目」或从下拉列表中选择。
2. **上传资料**：支持 PPT、PDF、DOCX、TXT，可批量上传。
3. **建立知识库**：选择资料范围后点击「添加/更新当前范围」或「重建当前范围」。
4. **选择资料范围**：可按全部资料、单个资料、多选资料或系统识别分组筛选。
5. **使用核心功能**：
   - **资料概览**：快速了解当前资料范围的主线、核心概念
   - **复习提纲**：生成考前 checklist 和重点
   - **智能问答**：输入问题，获得基于资料的答案
   - **资料整理（Longform）**：生成深度分析、学习笔记等长文
   - **自测题**：配置题型和出题模式，自动生成
6. **查看来源**：点击答案中的 `[1]`、`E1` 等编号查看原文或页面预览。
7. **导出结果**：Word 或 PDF 直接下载。
8. **历史记录**：查看之前生成的问题和答案。

---

## 自测题

自测题是独立的后端服务，支持完整的页面级组卷。

### 单页出题（single_page，默认）

- Chunk 按 `source_path + slide_number`（PPT）或 `source_path + page_number`（PDF）分组
- 同一页的多个 Chunk 自动合并去重（上限 2400 字符）
- 每道题绑定一个页面，可使用该页内的多个 E 编号证据
- 页面数量足够时优先不重复页面，不足时均衡复用

### 融合出题（fusion）

- 先由 LLM 提取每页的概念卡（角色、核心概念、关键事实）
- 通过角色对映射关系类型（定义与示例、条件与结果、原理与应用等）
- 构建 2～3 页的融合组，考查跨页面综合理解
- 填空题始终走单页，简答/大题优先融合，选择题约一半融合
- 无可靠融合关系时自动安全降级为 single_page，warning 会说明

### 来源预览

- 自测题解析中的 `E1`、`[E1]`、`【E1】` 等编号可点击
- 点击后直接打开对应 PPT/PDF 页面预览
- 若无法生成页面预览，则展开来源依据区域并滚动到对应卡片高亮

### 答案模式

- **inline**：每道题后立即显示答案
- **end**：题目与答案分区域
- **dual**：同时生成「练习版」（无答案）和「解析版」（含答案）

详细实施报告见 `docs/SELF_TEST_PAGE_LEVEL_GENERATION_IMPLEMENTATION_REPORT.md`。

---

## 导出

| 格式 | 路由 | 说明 |
|---|---|---|
| Word（自测题） | `POST /api/export/self-test/docx` | 自测题专用导出 |
| Word（通用） | `POST /api/export/document/docx` | 概览/提纲等通用导出 |
| PDF | `POST /api/export/document/pdf` | 临时 DOCX 经 LibreOffice headless 转换 |

PDF 导出依赖 LibreOffice（已验证 26.2.3.2 正常）。若导出接口返回 404，请先检查 8000 端口是否运行了旧后端进程，重启正确项目目录的后端后重试。

---

## AI 配置

- **供应商**：支持 DeepSeek（默认）和 OpenAI-compatible
- **模式**：快速模式（deepseek-v4-flash）/ 深度模式（deepseek-v4-pro），也可自定义模型
- **API Key**：存储在本地 `config/ai_settings.local.json` 中，前端只接收 `has_api_key` 布尔值，不返回原文
- **AI 开关**：关闭后不影响 RAG 检索、来源查看、页面预览等非 LLM 功能
- **网络错误重试**：DeepSeek 请求内置自动重试，最多 3 次，覆盖 SSL 错误、连接超时、HTTP 429/5xx

---

## 常见问题

### 后端启动慢

默认 Uvicorn 的 `--reload` 会监控整个项目根目录（包括 `subjects/` 下的资料文件）。建议使用 `--reload-dir backend --reload-dir src` 只监控代码目录，或关闭 `--reload` 使用稳定模式。

### 接口 404 / OpenAPI 中缺少新路由

8000 端口可能运行了未加载最新代码的旧后端进程。先停止旧进程，再重启正确项目目录下的后端。确认方法：`http://127.0.0.1:8000/openapi.json`。

### PDF 导出失败

确认已安装 LibreOffice 且 `soffice --version` 可正常输出。如果超时，可以再次重试。

### PPT/PDF 无可检索文本

当前不支持 OCR。扫描版 PDF、截图型 PPT 无法提取文字，需要先转成可复制文本的文件。

### 知识库为 0 Chunk

请检查：资料范围是否选对、资料是否为空文件、PDF 是否为扫描版、是否已执行建库操作。

### DeepSeek 未配置

页面顶部状态栏会提示。编辑 `.env` 中的 `DEEPSEEK_API_KEY` 后重启后端。

### 页面预览失败

- PPT/PPTX 预览依赖 LibreOffice 转换，首次请求可能较慢
- 预览缓存位于 `subjects/{科目}/outputs/page_preview_cache/`，30 天过期自动清理
- DOCX/TXT 暂不支持页面预览

### 8000 端口被占用

使用以下命令查找占用进程：

```powershell
Get-NetTCPConnection -LocalPort 8000 -State Listen
```

获取 `OwningProcess` PID 后，在任务管理器中结束，或：

```powershell
Stop-Process -Id <PID> -Force
```

---

## 文档

| 文档 | 内容 |
|---|---|
| `docs/SELF_TEST_PAGE_LEVEL_GENERATION_IMPLEMENTATION_REPORT.md` | 自测题页面级组卷实施报告（含 single_page/fusion 流程、融合组结构、校验规则、测试清单） |
| `docs/课程资料RAG查询_下一会话交接_自测题页面级融合.md` | 页面级融合交接文档 |

---

## 验证状态

### 已完成

- `backend/app/main.py` — Python 语法通过（py_compile）
- `backend/app/services/self_test_service.py` — Python 语法通过（py_compile）
- `frontend` — `npx tsc --noEmit` 通过
- Word 导出 — 浏览器下载正常
- PDF 导出 — 浏览器下载正常（LibreOffice 26.2.3.2）
- PDF 路由 — OpenAPI 确认路由存在
- E 编号预览代码 — TypeScript 类型检查通过

### 未执行

- 尚未完成所有资料组合下的真实 LLM 全量回归测试（依赖 DeepSeek API key 和建好的资料库）
- fusion 仍需按手动测试清单覆盖更多科目和资料类型

---

## 数据安全与提交注意

以下内容不应提交到版本库：

- `.env` / `.env.*` — 环境变量和 API Key
- `.venv/` — Python 虚拟环境
- `frontend/node_modules/` — 前端依赖
- `config/ai_settings.local.json` — 本地 AI 配置（含 API Key）
- `subjects/*/materials/` — 课程原始资料
- `subjects/*/outputs/` — ChromaDB、缓存、解析结果
- `data/`、`*.db` — 本地 SQLite 数据库
- `*.log`、`*.tmp` — 日志和临时文件
- `.claude/` — Claude 本地配置
