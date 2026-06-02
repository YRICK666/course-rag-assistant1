# Course RAG Assistant

面向课程资料的本地 RAG 学习助手。按科目管理课程资料，将 PPT、PDF、Word、TXT 等文件解析为可检索向量知识库，通过 React + FastAPI 提供智能问答、资料概览、复习提纲、自测题生成、长文分析等功能，并保留来源引用方便核对。

## Features

- **多科目资料库** — 不同课程拥有独立的知识库
- **向量检索** — 基于 ChromaDB + BGE 中文嵌入模型
- **智能问答与来源依据** — 检索相关片段后由 LLM 生成答案，保留来源引用
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
| Vector DB | ChromaDB |
| Embedding | BAAI/bge-small-zh-v1.5 |
| LLM Provider | DeepSeek / OpenAI-compatible Chat Completions API |

## Requirements

- Git
- Python 3.10+
- Node.js 18+
- LibreOffice, optional, for `.ppt` to `.pptx` conversion
- Internet access, first launch may download the embedding model

## Quick Start

### Backend

PowerShell on Windows:

    python -m venv .venv
    .\.venv\Scripts\Activate.ps1
    pip install -r requirements.txt
    Copy-Item .env.example .env
    python -m uvicorn backend.app.main:app --reload --host 127.0.0.1 --port 8000

Linux / macOS:

    python -m venv .venv
    source .venv/bin/activate
    pip install -r requirements.txt
    cp .env.example .env
    python -m uvicorn backend.app.main:app --reload --host 127.0.0.1 --port 8000

### Frontend

    cd frontend
    npm install
    npm run dev

The app will be available at:

    http://localhost:5173

## Configuration

LLM provider settings are configured via `.env`, which is not committed.

    DEEPSEEK_API_KEY=your_api_key_here
    DEEPSEEK_BASE_URL=https://api.deepseek.com
    DEEPSEEK_MODEL=deepseek-v4-flash
    EMBEDDING_MODEL=BAAI/bge-small-zh-v1.5

If `DEEPSEEK_API_KEY` is left empty, the system will only return retrieved snippets and a brief local summary without LLM-generated answers.

## Usage

Create a subject in the app, upload course materials, build the vector index, then ask questions, generate overviews, study guides, self-test questions, or long-form analysis. Check source citations and page previews to verify answers.

## Security Notes

- No API keys are committed to this repository.
- No uploaded course materials are included.
- No vector database or index files are included.
- No local configuration is included.
- `.env` and local AI settings are ignored by Git.

## What is not included

This repository does not contain:

- Uploaded course materials or personal data
- Vector database files or pre-built indices
- API keys, tokens, or secrets
- Local environment configuration
- Internal development or workflow files
