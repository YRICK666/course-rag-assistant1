from __future__ import annotations

import logging
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
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
SUMMARY_MAX_WORKERS = 3

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


def _longform_limits(
    target_length: int,
) -> tuple[int, int]:
    """Return max groups and chunks for the requested output length."""
    if target_length <= 1500:
        return 6, 48

    if target_length <= 3000:
        return 10, 72

    if target_length <= 5000:
        return 14, 96

    return 16, 112


def _sample_items_evenly(
    items: list[dict[str, Any]],
    limit: int,
) -> list[dict[str, Any]]:
    """Keep representative items from the beginning, middle and end."""
    if limit <= 0:
        return []

    if len(items) <= limit:
        return list(items)

    if limit == 1:
        return [items[len(items) // 2]]

    positions = [
        round(index * (len(items) - 1) / (limit - 1))
        for index in range(limit)
    ]

    return [items[position] for position in positions]


def _limit_groups_for_target(
    groups: list[list[dict[str, Any]]],
    target_length: int,
) -> tuple[list[list[dict[str, Any]]], int, int]:
    """Reduce work while preserving balanced coverage across groups."""
    max_groups, max_chunks = _longform_limits(target_length)
    limited_groups = list(groups)

    if len(limited_groups) > max_groups:
        limited_groups = _sample_items_evenly(
            limited_groups,
            max_groups,
        )

    total_chunks = sum(len(group) for group in limited_groups)

    if total_chunks <= max_chunks or not limited_groups:
        return limited_groups, max_groups, max_chunks

    base_quota = max_chunks // len(limited_groups)
    extra_quota = max_chunks % len(limited_groups)
    sampled_groups: list[list[dict[str, Any]]] = []

    for index, group in enumerate(limited_groups):
        quota = base_quota + (1 if index < extra_quota else 0)
        quota = max(1, min(quota, len(group)))

        sampled_groups.append(
            _sample_items_evenly(group, quota)
        )

    return sampled_groups, max_groups, max_chunks


def group_chunks_by_source_or_page(
    chunks: list[dict[str, Any]],
) -> list[list[dict[str, Any]]]:
    """Group chunks by source and preserve balanced document coverage.

    Strategy:
    1. Group by source_path and sort by page / slide / chunk position.
    2. Split long sources into consecutive groups.
    3. If there are too many groups, sample groups evenly.
    4. If there are too many chunks, allocate a balanced quota to every
       retained group instead of keeping only the earliest groups.
    """
    if not chunks:
        return []

    by_source: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for chunk in chunks:
        source_path = (
            chunk.get("metadata", {}).get("source_path")
            or "unknown"
        )
        by_source[source_path].append(chunk)

    for source_chunks in by_source.values():
        source_chunks.sort(key=_chunk_sort_key)

    groups: list[list[dict[str, Any]]] = []

    for source_path in sorted(by_source):
        source_chunks = by_source[source_path]

        for start in range(0, len(source_chunks), MAX_CHUNKS_PER_GROUP):
            groups.append(
                source_chunks[start : start + MAX_CHUNKS_PER_GROUP]
            )

    if len(groups) > MAX_GROUPS:
        total_groups = len(groups)
        selected_indexes = [
            int(index * total_groups / MAX_GROUPS)
            for index in range(MAX_GROUPS)
        ]
        groups = [groups[index] for index in selected_indexes]

    total_chunks = sum(len(group) for group in groups)

    if total_chunks > MAX_CHUNKS and groups:
        base_quota = MAX_CHUNKS // len(groups)
        extra_quota = MAX_CHUNKS % len(groups)
        sampled_groups: list[list[dict[str, Any]]] = []

        for index, group in enumerate(groups):
            quota = base_quota + (1 if index < extra_quota else 0)
            quota = max(1, min(quota, len(group)))

            if len(group) <= quota:
                sampled_groups.append(group)
                continue

            if quota == 1:
                sampled_groups.append([group[len(group) // 2]])
                continue

            positions = [
                round(position * (len(group) - 1) / (quota - 1))
                for position in range(quota)
            ]

            sampled_groups.append([group[position] for position in positions])

        groups = sampled_groups

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
    stage: str = "unknown",
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
            logger.warning(
                "Longform LLM output reached token limit: "
                "stage=%s model=%s max_tokens=%d",
                stage,
                model,
                max_tokens,
            )
        return content
    except (KeyError, IndexError, ValueError) as exc:
        raise DeepSeekError(
            f"Longform LLM 响应异常（{get_llm_provider_label()}）: {exc}"
        ) from exc


_SUMMARY_SYSTEM = (
    "你是课程资料证据整理助手。当前阶段只处理一个资料分组，"
    "目标是制作供后续长文写作使用的高密度证据笔记。\n"
    "必须遵守：\n"
    "1. 只能依据用户提供的资料片段，不得使用资料外知识补齐内容。\n"
    "2. 优先保留定义、条件、步骤、因果关系、概念对比、结论、例子和限制。\n"
    "3. 不要因为追求简短而删除后续写作需要的关键信息。\n"
    "4. 资料没有提供的项目应明确写“资料未提供”，不得自行推断。\n"
    "5. 保留文件名及页码或幻灯片号等来源定位。\n"
    "6. 当前不是最终正文阶段，不写空泛引言、套话和总结性口号。\n"
    "7. 使用普通 Unicode 字符，不使用 LaTeX 包裹。"
)

_OUTLINE_SYSTEM = (
    "你是课程资料长文的结构编辑。当前阶段只生成总体大纲，不生成正文。\n"
    "必须遵守：\n"
    "1. 大纲必须建立在提供的分组证据笔记上，不得引入资料外主题。\n"
    "2. 根据指定的整理类型、目标长度和用户额外要求设计结构。\n"
    "3. 必须均衡覆盖不同资料分组，不能只围绕前几个分组展开。\n"
    "4. 优先按照资料真实的概念关系、逻辑关系和章节脉络组织结构。\n"
    "5. 避免套用“概念—特点—意义—总结”等与资料无关的通用模板。\n"
    "6. 合并重复主题，并为矛盾或不同表述预留比较位置。\n"
    "7. 只输出分级大纲，不输出正文或额外解释。"
)

_FINAL_SYSTEM = (
    "你是严谨的课程资料长文写作助手。请根据总体大纲和分组证据笔记"
    "生成完整成稿。\n"
    "必须遵守：\n"
    "1. 所有事实、概念、结论和例子必须来自提供的证据笔记。\n"
    "2. 不得补充资料外事实，不得虚构引文、页码、数据或参考文献。\n"
    "3. 不要机械拼接摘要，要跨分组整合、去重并建立自然逻辑。\n"
    "4. 同一概念原则上完整解释一次，后文只在必要时简要回指。\n"
    "5. 不同资料存在差异时，应如实呈现不同表述，不要擅自裁决。\n"
    "6. 在不违背资料事实的前提下，用户对文体、重点、结构和篇幅的"
    "额外要求具有最高优先级。\n"
    "7. 不生成 [1][2] 等虚假引用编号；来源由系统在正文之外单独展示。\n"
    "8. 资料不足时使用审慎表达，必要时只在结尾集中说明资料范围限制，"
    "不要在每一节反复免责声明。\n"
    "9. 直接输出完整成稿，不输出写作过程、提示词说明或待办事项。\n"
    "10. 使用普通 Unicode 字符，不使用 LaTeX 包裹。"
)

_TYPE_LABELS = {
    "analysis": "深度分析",
    "study_notes": "学习笔记",
    "report": "综合报告",
    "review": "读后感 / 心得体会",
    "outline": "知识框架",
}

_TYPE_WRITING_RULES = {
    "analysis": (
        "围绕资料中的核心问题展开，说明关键概念、理论脉络、"
        "作用机制、概念关系和主要结论。分析必须有资料依据，"
        "避免只做表面概括。"
    ),
    "study_notes": (
        "面向学习和复习组织内容，突出核心定义、重点知识、"
        "概念对比、步骤方法、易混点、易错点和记忆线索。"
        "适合使用清晰的小标题、列表和简洁解释。"
    ),
    "report": (
        "采用正式、客观的报告文体，可包含背景与目的、资料主要内容、"
        "综合分析和结论。避免口语化表达和无依据的主观评价。"
    ),
    "review": (
        "可以采用第一人称表达理解、收获和思考，但所有观点必须建立在"
        "资料内容上。不得虚构个人经历、课堂活动或实践案例。"
    ),
    "outline": (
        "以层级化知识框架为主，突出主题之间的从属、并列、因果和对比关系。"
        "减少连续长段落，优先使用分级标题和要点。"
    ),
}


def _longform_type_label(longform_type: str) -> str:
    return _TYPE_LABELS.get(longform_type, "综合整理")


def _longform_type_rule(longform_type: str) -> str:
    return _TYPE_WRITING_RULES.get(
        longform_type,
        "根据资料真实结构进行综合整理，保持内容完整、准确和连贯。",
    )


def _length_instruction(target_length: int) -> str:
    lower = max(500, int(target_length * 0.85))
    upper = max(lower, int(target_length * 1.15))

    return (
        f"目标长度约 {target_length} 字，建议控制在 {lower}—{upper} 字之间。"
        "优先保证资料覆盖和信息密度，不得通过重复观点、空泛过渡、"
        "反复总结或无实质内容的铺垫凑字。"
    )


def _user_instruction_block(user_instruction: str) -> str:
    cleaned = user_instruction.strip()

    if not cleaned:
        return "用户未提供额外要求。"

    return (
        "用户额外要求如下。在不违背资料事实的前提下，"
        "这些文体、重点、结构和篇幅要求具有最高优先级：\n"
        f"{cleaned}"
    )


def summarize_longform_group(
    group_index: int,
    group_chunks: list[dict[str, Any]],
) -> str:
    """Create high-density evidence notes for one group of chunks."""
    first_metadata = group_chunks[0].get("metadata", {})
    source_path = first_metadata.get("source_path", "unknown")
    file_name = first_metadata.get("file_name", "")
    source_label = (
        f"{file_name or source_path}（{source_path}）"
        if file_name and file_name != source_path
        else source_path
    )

    context_parts: list[str] = []

    for index, chunk in enumerate(group_chunks, 1):
        metadata = chunk.get("metadata", {})
        page_number = metadata.get("page_number")
        slide_number = metadata.get("slide_number")

        if page_number is not None:
            location = f"第 {page_number} 页"
        elif slide_number is not None:
            location = f"第 {slide_number} 张幻灯片"
        else:
            location = "位置未知"

        text = clean_math_text(
            " ".join((chunk.get("text") or "").split())
        )

        if len(text) > 1200:
            text = text[:1200] + "……"

        context_parts.append(
            f"[片段 {index}｜{location}]\n{text}"
        )

    context = "\n\n".join(context_parts)

    user_prompt = (
        f"资料分组编号：第 {group_index} 组\n"
        f"资料来源：{source_label}\n\n"
        f"资料片段：\n{context}\n\n"
        "请整理成一份高密度证据笔记，建议包含以下栏目：\n"
        "一、本组主题与内容范围\n"
        "二、核心定义与重要术语\n"
        "三、关键论点、结论及其逻辑关系\n"
        "四、条件、步骤、机制、概念对比或因果关系\n"
        "五、资料中出现的重要例子、案例、公式或说明\n"
        "六、容易混淆的内容、限制、例外或不同表述\n"
        "七、可供最终长文使用的核心材料\n"
        "八、来源定位\n\n"
        "资料没有涉及的栏目可省略，不要为了补齐栏目而编造内容。"
        "请严格控制在 450—700 个汉字，最多保留 8 个栏目；"
        "达到长度上限后立即停止，不写结语、套话或重复总结。"
        "资料信息较少时可以更短，但不得因压缩而丢失关键定义、"
        "条件、步骤和论证关系。"
    )

    return _call_llm(
        _SUMMARY_SYSTEM,
        user_prompt,
        temperature=0.2,
        max_tokens=1800,
        stage=f"summary_group_{group_index}",
    )


def synthesize_longform_outline(
    group_summaries: list[dict],
    *,
    longform_type: str,
    target_length: int,
    user_instruction: str,
) -> str:
    """Generate a type-aware outline from valid evidence notes."""
    summaries_text = "\n\n".join(
        (
            f"[第 {summary['group_index']} 组｜"
            f"{summary['source_label']}]\n"
            f"{summary['summary']}"
        )
        for summary in group_summaries
    )

    type_label = _longform_type_label(longform_type)
    type_rule = _longform_type_rule(longform_type)

    user_prompt = (
        f"整理类型：{type_label}\n"
        f"写作要求：{type_rule}\n"
        f"{_length_instruction(target_length)}\n\n"
        f"{_user_instruction_block(user_instruction)}\n\n"
        f"分组证据笔记：\n{summaries_text}\n\n"
        "请据此生成总体大纲。\n"
        "具体要求：\n"
        "1. 大纲结构必须适合当前整理类型，而不是通用模板。\n"
        "2. 一级章节数量应与目标长度匹配，避免章节过多导致内容空泛。\n"
        "3. 所有主要资料分组都应在大纲中得到合理覆盖。\n"
        "4. 重复内容应合并，不同材料的差异应安排在比较或辨析部分。\n"
        "5. 每个一级标题下给出二级要点，说明本节要解决的具体内容。\n"
        "6. 不得加入证据笔记中没有出现的主题。\n"
        "7. 直接输出分级大纲，不要输出正文和解释。"
    )

    return _call_llm(
        _OUTLINE_SYSTEM,
        user_prompt,
        temperature=0.2,
        max_tokens=2400,
        stage="outline",
    )


def _first_summary_heading(summary_text: str) -> str:
    for raw_line in summary_text.splitlines():
        line = raw_line.strip().lstrip("#*-0123456789.、 ")

        if line:
            return line[:60]

    return ""


def build_fallback_outline(
    group_summaries: list[dict],
    longform_type: str,
) -> str:
    """Create a deterministic outline when LLM outline synthesis fails."""
    type_label = _longform_type_label(longform_type)
    lines = [f"# {type_label}", "## 一、资料主题与核心概念"]

    for index, summary in enumerate(group_summaries[:10], 1):
        heading = _first_summary_heading(summary.get("summary", ""))
        source_label = summary.get("source_label", f"第 {index} 组资料")
        lines.append(
            f"### {index}. {heading or source_label}"
        )

    lines.extend(
        [
            "## 二、主要概念之间的关系与重点辨析",
            "## 三、资料内容的综合整理",
            "## 四、结论与资料范围说明",
        ]
    )

    return "\n".join(lines)


def generate_longform_from_summaries(
    outline: str,
    group_summaries: list[dict],
    longform_type: str,
    target_length: int,
    user_instruction: str,
) -> str:
    """Generate final longform content from outline and evidence notes."""
    summaries_text = "\n\n".join(
        (
            f"[第 {summary['group_index']} 组｜"
            f"{summary['source_label']}]\n"
            f"{summary['summary']}"
        )
        for summary in group_summaries
    )

    type_label = _longform_type_label(longform_type)
    type_rule = _longform_type_rule(longform_type)

    user_prompt = (
        f"整理类型：{type_label}\n"
        f"该类型的写作规则：{type_rule}\n"
        f"{_length_instruction(target_length)}\n\n"
        f"{_user_instruction_block(user_instruction)}\n\n"
        f"总体大纲：\n{outline}\n\n"
        f"分组证据笔记：\n{summaries_text}\n\n"
        f"请生成完整的{type_label}。\n"
        "写作要求：\n"
        "1. 按照总体大纲组织完整成稿，但大纲存在明显重复时应主动合并。\n"
        "2. 跨分组整合相同主题，不得按“第1组、第2组”机械逐组复述。\n"
        "3. 对核心概念给出资料范围内足够清晰的解释，并说明相关概念的关系。\n"
        "4. 保留重要条件、步骤、机制、对比、例子和限制，不得只写抽象结论。\n"
        "5. 不得添加资料外事实、虚构数据、虚构页码或虚构参考文献。\n"
        "6. 不要生成 [1][2] 之类的引用编号，来源由系统另外展示。\n"
        "7. 避免空泛开场、重复结论和模板化套话。\n"
        "8. 资料存在不同表述时，应明确说明材料之间的差异。\n"
        "9. 直接输出最终成稿，不输出写作计划或后续建议。"
    )

    max_tokens = min(
        max(4096, int(target_length * 2)),
        12000,
    )

    return _call_llm(
        _FINAL_SYSTEM,
        user_prompt,
        temperature=0.35,
        max_tokens=max_tokens,
        timeout=300,
        stage="final",
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
    groups, max_groups, max_chunks = _limit_groups_for_target(
        group_chunks_by_source_or_page(all_chunks),
        target_length,
    )
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
    pipeline_warnings: list[str] = []
    failed_group_indexes: list[int] = []
    summary_results: dict[int, dict] = {}

    worker_count = max(
        1,
        min(SUMMARY_MAX_WORKERS, len(groups)),
    )

    logger.info(
        "Longform summary stage: groups=%d workers=%d "
        "group_limit=%d chunk_limit=%d",
        len(groups),
        worker_count,
        max_groups,
        max_chunks,
    )

    with ThreadPoolExecutor(
        max_workers=worker_count,
        thread_name_prefix="longform-summary",
    ) as executor:
        future_map = {
            executor.submit(
                summarize_longform_group,
                index + 1,
                group,
            ): (index + 1, group)
            for index, group in enumerate(groups)
        }

        for future in as_completed(future_map):
            group_index, group = future_map[future]
            metadata = group[0].get("metadata", {}) or {}
            source_path = (
                metadata.get("source_path")
                or "unknown"
            )
            file_name = (
                metadata.get("file_name")
                or ""
            )
            source_label = (
                f"{file_name}（{source_path}）"
                if file_name and file_name != source_path
                else source_path
            )

            try:
                summary = future.result()
            except Exception as exc:
                logger.warning(
                    "Group %d summarization failed: %s",
                    group_index,
                    exc,
                )
                failed_group_indexes.append(group_index)
                continue

            if not summary or not summary.strip():
                failed_group_indexes.append(group_index)
                continue

            summary_results[group_index] = {
                "group_index": group_index,
                "source_label": source_label,
                "chunks_count": len(group),
                "summary": summary,
            }

    group_summaries = [
        summary_results[index]
        for index in sorted(summary_results)
    ]

    if failed_group_indexes:
        pipeline_warnings.append(
            "以下资料分组摘要生成失败，未参与后续大纲和正文生成："
            + "、".join(
                str(index)
                for index in sorted(failed_group_indexes)
            )
            + "。"
        )

    if not group_summaries:
        sources = _build_sources(groups) if include_sources else []

        if include_sources:
            pipeline_warnings.append(LONGFORM_SOURCE_WARNING)

        pipeline_warnings.insert(
            0,
            "所有资料分组的摘要均生成失败，未继续生成大纲和正文。",
        )

        return {
            "content": "",
            "outline": "",
            "group_summaries": [],
            "sources": sources,
            "warnings": pipeline_warnings,
            "stats": {
                "total_chunks": total_chunks,
                "used_chunks": used_chunks,
                "groups_count": len(groups),
            },
        }

    # -- Step 4: Synthesize outline -----------------------------------------
    try:
        outline = synthesize_longform_outline(
            group_summaries,
            longform_type=longform_type,
            target_length=target_length,
            user_instruction=user_instruction,
        )
    except DeepSeekError as exc:
        logger.warning("Outline synthesis failed: %s", exc)
        outline = build_fallback_outline(
            group_summaries,
            longform_type,
        )
        pipeline_warnings.append(
            "总体大纲由模型生成失败，系统已使用本地降级大纲继续整理。"
        )

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
        content = ""
        pipeline_warnings.append(
            f"最终正文生成失败：{exc}"
        )
    # -- Step 6: Build sources ----------------------------------------------
    sources = _build_sources(groups) if include_sources else []

    warnings: list[str] = list(pipeline_warnings)
    if target_length > 8000:
        warnings.append(
            "目标字数较长，单次生成可能受模型窗口限制影响。"
        )
    if used_chunks < total_chunks:
        warnings.append(
            f"本次长文使用了 {used_chunks}/{total_chunks} 个资料片段"
            f"（本次限制：最多 {max_chunks} 片段，{max_groups} 组）。"
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
