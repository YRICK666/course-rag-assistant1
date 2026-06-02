# Course RAG Assistant

面向课程资料的本地 RAG 学习助手。按科目管理课程资料，将 PPT、PDF、Word、TXT 等文件解析为可检索向量知识库，通过 React + FastAPI 提供智能问答、资料概览、复习提纲、自测题生成、长文分析等功能，并保留来源引用方便核对。

## Features

- **多科目资料库** — 不同课程拥有独立的知识库
- **向量检索** — 基于 ChromaDB + BGE 中文嵌入模型
- **智能问答** — 检索相关片段后由 LLM 生成答案，保留来源引用
- **Source Preview** — PDF/PPT 页面级预览，方便核对原始资料
- **资料概览** — 快速了解当前资料范围的核心内容
- **复习提纲** — 考前知识梳理与重难点总结
- **自测题生成** — 自定义题型与数量的练习生成
- **Longform 资料整理** — 跨资料深度分析、学习笔记、综合报告、长文撰写
- **Word / PDF 导出** — 概览、提纲、自测题、长文均可导出

## Tech Stack

| Layer | Technology |
|---|---|
| Frontend | React 18 + TypeScript + Vite |
| Backend | FastAPI (Python) |
| Vector DB | ChromaDB (per-subject) |
| Embedding | BAAI/bge-small-zh-v1.5 |
| LLM Provider | DeepSeek / OpenAI-compatible Chat Completions API |

## Quick Start

### Prerequisites

- Python 3.10+
- Node.js 18+
- LibreOffice (optional, for `.ppt` → `.pptx` conversion)

### Backend

```bash
# Create virtual environment
python -m venv .venv
source .venv/bin/activate    # Linux/macOS
.venv\Scripts\Activate.ps1   # Windows

# Install dependencies
pip install -r requirements.txt

# Configure LLM provider
cp .env.example .env
# Edit .env with your API key and model settings

# Start backend
uvicorn backend.app.main:app --reload --port 8000
```

### Frontend

```bash
cd frontend
npm install
npm run dev
```

The app will be available at `http://localhost:5173`.

## Security

- **No API keys** are committed to this repository
- **No course materials** are included — you must upload your own
- **No vector database/indices** are included
- **No local configuration** is included
- The `.env` file and local AI settings are in `.gitignore`

## Usage

1. Create a subject (course) in the app
2. Upload course materials (PDF, PPT, PPTX, DOCX, TXT)
3. Build the vector index for the selected materials
4. Ask questions, generate overviews, study guides, or long-form analysis
5. Check source citations and page previews to verify answers

## Configuration

LLM provider settings are configured via `.env` file (not committed):

```env
DEEPSEEK_API_KEY=your_api_key_here
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-v4-flash
EMBEDDING_MODEL=BAAI/bge-small-zh-v1.5
```

> **Note:** This is a public template repository. All API keys, uploaded course materials, and vector indices are local to your machine and are excluded from version control.
