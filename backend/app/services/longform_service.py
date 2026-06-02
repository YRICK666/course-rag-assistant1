from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any

from ai_settings import load_ai_settings
from llm_deepseek import (
    DeepSeekError,
    clean_math_text,
    get_deepseek_model,
    get_llm_provider_label,
    post_chat_completions,
)
from retriever import fetch_all_records, get_collection, list_indexed_sources

logger = logging.getLogger(__name__)

MAX_GROUPS = 20
MAX_CHUNKS = 120
MAX_CHUNKS_PER_GROUP = 8
MAX_SOURCES = 50

LONGFORM_SOURCE_WARNING = (
    "Longform 来源为本次长文整理使用的主要资料片段，不代表逐句精确引用。"
)


def collect_chunks_by_scope(
    subject_paths,
    source_filters: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Collect all chunks for a subject, optionally filtered by source_path.

    Returns list of dicts with keys: id, text, metadata.
    """
    collection = get_collection(create=False, outputs_dir=subject_paths.outputs_dir)
    if collection.count() == 0:
        return []

    source_paths: set[str] | None = None
    if source_filters:
        indexed_sources = list_indexed_sources(outputs_dir=subject_paths.outputs_dir)
        all_indexed_paths = {
            s["source_path"] for s in indexed_sources if s.get("source_path")
        }
        source_paths = {
            p
            for p in all_indexed_paths
            if any(f in p for f in source_filters)
        }
        if not source_paths:
            source_paths = {
                p
                for p in all_indexed_paths
                if any(f.lower() in p.lower() for f in source_filters)
            }

    records = fetch_all_records(collection, source_paths=source_paths)
    chunks: list[dict[str, Any]] = []
    for record in records:
        metadata = record.get("metadata", {}) or {}
        chunks.append({
            "id": record.get("id"),
            "text": record.get("text", ""),
            "metadata": {
                "source_path": metadata.get("source_path", ""),
                "file_name": metadata.get("file_name", ""),
                "file_type": metadata.get("file_type", ""),
                "page_number": metadata.get("page_number"),
                "slide_number": metadata.get("slide_number"),
                "chunk_index": metadata.get("chunk_index"),
            },
        })
    return chunks


def _chunk_sort_key(chunk: dict[str, Any]) -> tuple[int, int, int]:
    """Sort key for chunks: page_number, slide_number, chunk_index."""
    md = chunk.get("metadata", {})
    try:
        return (
            int(md.get("page_number") or 0),
            int(md.get("slide_number") or 0),
            int(md.get("chunk_index") or 0),
        )
    except (TypeError, ValueError):
        return (0, 0, 0)


def group_chunks_by_source_or_page(chunks: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """Group chunks and sample if over limits.

    Strategy:
    1. Group by source_path, sort within each source.
    2. Split sources exceeding MAX_CHUNKS_PER_GROUP into subgroups.
    3. If groups > MAX_GROUPS, sample evenly.
    4. If total chunks > MAX_CHUNKS, sample evenly across groups.
    """
    if not chunks:
        return []

    by_source: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for chunk in chunks:
        sp = chunk.get("metadata", {}).get("source_path", "unknown")
        by_source[sp].append(chunk)

    for sp in by_source:
        by_source[sp].sort(key=_chunk_sort_key)

    groups: list[list[dict[str, Any]]] = []
    for sp in sorted(by_source.keys()):
        source_chunks = by_source[sp]
        for i in range(0, len(source_chunks), MAX_CHUNKS_PER_GROUP):
            groups.append(source_chunks[i : i + MAX_CHUNKS_PER_GROUP])

    if len(groups) > MAX_GROUPS:
        total = len(groups)
        indices = [int(i * total / MAX_GROUPS) for i in range(MAX_GROUPS)]
        groups = [groups[i] for i in indices]

    total_chunks = sum(len(g) for g in groups)
    if total_chunks > MAX_CHUNKS:
        sampled: list[list[dict[str, Any]]] = []
        remaining = MAX_CHUNKS
        for group in groups:
            if remaining <= 0:
                break
            if len(group) <= remaining:
                sampled.append(group)
                remaining -= len(group)
            else:
                step = len(group) / remaining
                sampled_group = [group[int(i * step)] for i in range(remaining)]
                sampled.append(sampled_group)
                remaining = 0
        groups = sampled

    return groups


# ---------------------------------------------------------------------------
# LLM helper functions
# ---------------------------------------------------------------------------


def _call_llm(
    system_prompt: str,
    user_prompt: str,
    *,
    temperature: float = 0.3,
    max_tokens: int = 2048,
    timeout: int = 120,
) -> str:
    """Shared LLM call helper for longform pipeline stages."""
    model = get_deepseek_model()
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": False,
    }
    try:
        data = post_chat_completions(payload, timeout=timeout)
        content = clean_math_text(data["choices"][0]["message"]["content"].strip())
        finish_reason = data["choices"][0].get("finish_reason", "")
        if finish_reason == "length":
            content += (
                "\n\n---\n"
                "*本次输出受单次长度限制影响，可继续生成下一部分。*"
            )
        return content
    except (KeyError, IndexError, ValueError) as exc:
        raise DeepSeekError(
            f"Longform LLM 响应异常（{get_llm_provider_label()}）: {exc}"
        ) from exc


_SYSTEM_BASE = (
    "你是课程资料整理助手。你的任务是基于提供的资料片段进行整理和分析。\n"
    "1. 这是资料依据整理模式，不是普通问答。\n"
    "2. 不要误判成「资料里有没有这个要求」——用户要求的文体、字数属于输出形式要求。\n"
    "3. 长文必须综合多个资料分组的内容。\n"
    "4. 资料不足时说明限制，但继续基于已收集资料整理。\n"
    "5. 不要编造资料外的事实。\n"
    "6. 不要输出空泛大纲，要输出正文。\n"
    "7. 使用 Unicode 普通字符，不用 LaTeX 包裹。"
)


def summarize_longform_group(group_index: int, group_chunks: list[dict[str, Any]]) -> str:
    """Generate a local summary for one group of chunks."""
    source_path = group_chunks[0].get("metadata", {}).get("source_path", "unknown")
    file_name = group_chunks[0].get("metadata", {}).get("file_name", "")
    source_label = f"{file_name}（{source_path}）"

    context_parts: list[str] = []
    for i, chunk in enumerate(group_chunks, 1):
        md = chunk.get("metadata", {})
        page = md.get("page_number")
        slide = md.get("slide_number")
        location = (
            f"第 {page} 页"
            if page
            else (f"第 {slide} 张幻灯片" if slide else "位置未知")
        )
        text = clean_math_text(" ".join(chunk.get("text", "").split()))
        if len(text) > 800:
            text = text[:800] + "..."
        context_parts.append(f"[片段 {i}] {location}：\n{text}")

    context = "\n\n".join(context_parts)
    system_prompt = _SYSTEM_BASE
    user_prompt = (
        f"以下是一组来自同一资料「{source_label}」的连续片段。\n\n"
        f"{context}\n\n"
        "请生成该组资料的简要摘要，包括：\n"
        "1. 本组主题\n"
        "2. 关键概念\n"
        "3. 重要论点 / 理论\n"
        "4. 可以用于综合长文的材料\n"
        "5. 来源位置（文件名 + 页码 / 幻灯片号）\n\n"
        "摘要应简洁，200-400 字。不要编造资料中没有的内容。"
    )
    return _call_llm(system_prompt, user_prompt, temperature=0.3, max_tokens=1024)


def synthesize_longform_outline(group_summaries: list[dict]) -> str:
    """Generate an overall outline from group summaries."""
    summaries_text = "\n\n".join(
        f"[第 {s['group_index']} 组] {s['source_label']}\n{s['summary']}"
        for s in group_summaries
    )
    system_prompt = _SYSTEM_BASE
    user_prompt = (
        f"以下是对多份资料的分组摘要：\n\n{summaries_text}\n\n"
        "请基于以上所有分组摘要，生成一份长文的总体大纲。\n\n"
        "要求：\n"
        "1. 大纲应综合多个组的内容，不应只围绕前几组展开。\n"
        "2. 使用分级标题。\n"
        "3. 每个主要章节应覆盖来自不同资料的主题。\n"
        "4. 大纲应该逻辑连贯，结构合理。\n"
        "5. 不要编造资料中没有的主题。\n\n"
        "直接输出大纲，不需要额外解释。"
    )
    return _call_llm(system_prompt, user_prompt, temperature=0.3, max_tokens=2048)


def generate_longform_from_summaries(
    outline: str,
    group_summaries: list[dict],
    longform_type: str,
    target_length: int,
    user_instruction: str,
) -> str:
    """Generate the final longform text from outline + group summaries."""
    summaries_text = "\n\n".join(
        f"[第 {s['group_index']} 组] {s['source_label']}\n{s['summary']}"
        for s in group_summaries
    )

    type_labels = {
        "analysis": "深度分析",
        "study_notes": "学习笔记",
        "report": "综合报告",
        "review": "复习整理",
        "outline": "知识框架",
    }
    type_label = type_labels.get(longform_type, "综合整理")

    if target_length >= 5000:
        length_instr = (
            "目标字数不少于 4000 字，请尽量展开每个章节。"
            "如果无法一次完成，在结尾提示可继续生成下一部分。"
        )
    elif target_length >= 3000:
        length_instr = "目标字数约 3000 字，每个章节应充分展开。"
    elif target_length >= 1500:
        length_instr = "目标字数约 1500 字，内容应完整但相对精炼。"
    else:
        length_instr = "目标字数约 1000 字，内容精炼。"

    extra = ""
    if user_instruction:
        extra = f"\n用户额外要求：\n{user_instruction}"

    system_prompt = _SYSTEM_BASE
    user_prompt = (
        f"你正在以「{type_label}」的形式整理课程资料。\n\n"
        f"总体大纲：\n{outline}\n\n"
        f"分组摘要：\n{summaries_text}\n\n"
        f"请基于以上大纲和分组摘要，生成完整的{type_label}。\n\n"
        "要求：\n"
        "1. 使用分级标题。\n"
        "2. 事实必须来自分组摘要，不编造资料外的事实、页码、引文。\n"
        "3. 综合多个分组摘要，不要只围绕前几组展开。\n"
        f"4. {length_instr}\n"
        "5. 资料不足时说明限制，但继续基于已有资料整理。\n"
        "6. 不要输出空泛大纲，要输出正文。\n"
        "7. 引用来源时使用角标编号如 [1][2]。"
        f"{extra}"
    )

    max_tokens = min(max(4096, target_length * 2), 12000)
    return _call_llm(
        system_prompt,
        user_prompt,
        temperature=0.4,
        max_tokens=max_tokens,
        timeout=300,
    )


# ---------------------------------------------------------------------------
# Source building
# ---------------------------------------------------------------------------


def _build_sources(groups: list[list[dict[str, Any]]]) -> list[dict]:
    """Build deduplicated sources list from grouped chunks (max MAX_SOURCES)."""
    seen: set[str] = set()
    sources: list[dict] = []

    for group in groups:
        for chunk in group:
            md = chunk.get("metadata", {})
            sp = md.get("source_path", "")
            if sp in seen:
                continue
            seen.add(sp)
            file_name = md.get("file_name", "")
            file_type = md.get("file_type", "")

            sources.append({
                "rank": len(sources) + 1,
                "source": (
                    f"{file_name}（{file_type}）" if file_type else file_name
                ),
                "text": (chunk.get("text") or "")[:300],
                "metadata": {
                    "source_path": sp,
                    "file_name": file_name,
                    "file_type": file_type,
                    "page_number": md.get("page_number"),
                    "slide_number": md.get("slide_number"),
                },
            })
            if len(sources) >= MAX_SOURCES:
                return sources

    return sources


# ---------------------------------------------------------------------------
# Main pipeline entry point
# ---------------------------------------------------------------------------


def generate_longform_analysis(
    subject_paths,
    *,
    source_filters: list[str] | None = None,
    longform_type: str = "analysis",
    target_length: int = 3000,
    include_sources: bool = True,
    user_instruction: str = "",
) -> dict:
    """Run the full staged longform pipeline and return the result dict.

    Returns {content, outline, group_summaries, sources, warnings, stats}.
    """
    # -- Early checks -------------------------------------------------------
    ai_enabled = load_ai_settings()["enabled"]
    if not ai_enabled:
        return {
            "content": "",
            "outline": "",
            "group_summaries": [],
            "sources": [],
            "warnings": ["AI 已关闭，无法生成长文。"],
            "stats": {"total_chunks": 0, "used_chunks": 0, "groups_count": 0},
        }

    # -- Step 1: Collect chunks --------------------------------------------
    all_chunks = collect_chunks_by_scope(subject_paths, source_filters=source_filters)
    total_chunks = len(all_chunks)

    if total_chunks == 0:
        warnings = ["当前科目没有可用的知识库资料，请先建库。"]
        if source_filters:
            warnings.append(
                f"指定的资料筛选条件未匹配到任何索引内容：{source_filters}"
            )
        return {
            "content": "",
            "outline": "",
            "group_summaries": [],
            "sources": [],
            "warnings": warnings,
            "stats": {
                "total_chunks": 0,
                "used_chunks": 0,
                "groups_count": 0,
            },
        }

    # -- Step 2: Group chunks -----------------------------------------------
    groups = group_chunks_by_source_or_page(all_chunks)
    used_chunks = sum(len(g) for g in groups)

    if not groups:
        return {
            "content": "",
            "outline": "",
            "group_summaries": [],
            "sources": [],
            "warnings": ["资料分组后为空，无法生成长文。"],
            "stats": {
                "total_chunks": total_chunks,
                "used_chunks": 0,
                "groups_count": 0,
            },
        }

    # -- Step 3: Summarize each group ---------------------------------------
    group_summaries: list[dict] = []
    for idx, group in enumerate(groups):
        sp = group[0].get("metadata", {}).get("source_path", "unknown")
        fn = group[0].get("metadata", {}).get("file_name", "")
        try:
            summary = summarize_longform_group(idx + 1, group)
            group_summaries.append({
                "group_index": idx + 1,
                "source_label": f"{fn}（{sp}）",
                "chunks_count": len(group),
                "summary": summary,
            })
        except DeepSeekError as exc:
            logger.warning("Group %d summarization failed: %s", idx + 1, exc)
            group_summaries.append({
                "group_index": idx + 1,
                "source_label": f"{fn}（{sp}）",
                "chunks_count": len(group),
                "summary": f"（摘要生成失败：{exc}）",
            })

    # -- Step 4: Synthesize outline -----------------------------------------
    try:
        outline = synthesize_longform_outline(group_summaries)
    except DeepSeekError as exc:
        logger.warning("Outline synthesis failed: %s", exc)
        outline = f"（大纲生成失败：{exc}）"

    # -- Step 5: Generate longform content ----------------------------------
    try:
        content = generate_longform_from_summaries(
            outline=outline,
            group_summaries=group_summaries,
            longform_type=longform_type,
            target_length=target_length,
            user_instruction=user_instruction,
        )
    except DeepSeekError as exc:
        logger.warning("Longform generation failed: %s", exc)
        content = f"（长文生成失败：{exc}）"

    # -- Step 6: Build sources ----------------------------------------------
    sources = _build_sources(groups) if include_sources else []

    warnings: list[str] = []
    if target_length > 8000:
        warnings.append(
            "目标字数较长，单次生成可能受模型窗口限制影响。"
        )
    if used_chunks < total_chunks:
        warnings.append(
            f"本次长文使用了 {used_chunks}/{total_chunks} 个资料片段"
            f"（限制：最多 {MAX_CHUNKS} 片段，{MAX_GROUPS} 组）。"
        )
    if include_sources:
        warnings.append(LONGFORM_SOURCE_WARNING)

    return {
        "content": content,
        "outline": outline,
        "group_summaries": group_summaries,
        "sources": sources,
        "warnings": warnings,
        "stats": {
            "total_chunks": total_chunks,
            "used_chunks": used_chunks,
            "groups_count": len(groups),
        },
    }
