from __future__ import annotations

import json
import logging
import re
from collections import Counter, defaultdict
from typing import Any

from llm_deepseek import (
    DeepSeekError,
    clean_math_text,
    get_deepseek_model,
    get_llm_provider_label,
    post_chat_completions,
)

from .longform_service import collect_chunks_by_scope

logger = logging.getLogger(__name__)

MIN_EVIDENCE_CHUNKS = 12
MAX_EVIDENCE_CHUNKS = 36
CHUNKS_PER_QUESTION = 3
MAX_CHARS_PER_CHUNK = 750
MAX_CHARS_PER_PAGE = 2400

TYPE_LABELS = {
    "choice": "选择题",
    "fill": "填空题",
    "essay": "简答/大题",
}

TYPE_ALIASES = {
    "choice": "choice",
    "选择题": "choice",
    "单选题": "choice",
    "fill": "fill",
    "填空题": "fill",
    "essay": "essay",
    "简答题": "essay",
    "大题": "essay",
    "简答/大题": "essay",
}

DIFFICULTY_ALIASES = {
    "easy": "easy",
    "基础": "easy",
    "简单": "easy",
    "medium": "medium",
    "中等": "medium",
    "一般": "medium",
    "hard": "hard",
    "困难": "hard",
    "综合": "hard",
}

ANSWER_MODE_RULES = {
    "inline": (
        "固定标题为 ## 解析版。每道题后立即显示【答案】【解析】【考点】【依据】。"
    ),
    "end": (
        "固定标题为 ## 解析版。先输出所有题目，不夹杂答案；最后单独输出 "
        "## 答案与解析，并逐题给出【答案】【解析】【考点】【依据】。"
    ),
    "dual": (
        "先输出 ## 练习版，只包含题目，不显示答案、解析、考点和依据；"
        "再输出 ## 解析版，逐题显示完整题目和【答案】【解析】【考点】【依据】。"
    ),
}


PAGE_CONCEPT_ROLES = {
    "definition",
    "condition",
    "mechanism",
    "process",
    "comparison",
    "example",
    "application",
    "conclusion",
}

FUSION_RELATION_TYPES = {
    "definition_example",
    "definition_application",
    "condition_result",
    "process_sequence",
    "mechanism_application",
    "theory_case",
    "comparison",
    "cause_effect",
}

ROLE_RELATION_TYPES = {
    frozenset({"definition", "example"}): "definition_example",
    frozenset({"definition", "application"}): "definition_application",
    frozenset({"condition", "conclusion"}): "condition_result",
    frozenset({"condition", "mechanism"}): "cause_effect",
    frozenset({"mechanism", "application"}): "mechanism_application",
    frozenset({"mechanism", "example"}): "theory_case",
    frozenset({"comparison"}): "comparison",
}

RELATION_LABELS = {
    "definition_example": "定义与示例",
    "definition_application": "定义与应用",
    "condition_result": "条件与结果",
    "process_sequence": "过程衔接",
    "mechanism_application": "原理与应用",
    "theory_case": "理论与案例",
    "comparison": "概念比较",
    "cause_effect": "条件与作用机制",
}

MAX_FUSION_GROUPS = 12

BLUEPRINT_SYSTEM_PROMPT = (
    "你是课程自测题组卷蓝图设计助手。当前阶段只设计蓝图，不写正式题目。"
    "只能依据用户提供的证据片段，不得引入资料外知识。"
    "必须严格输出可解析 JSON，不要使用 Markdown 代码块。"
)

FINAL_SYSTEM_PROMPT = (
    "你是严谨的课程自测题命题助手。必须严格依据组卷蓝图和证据片段命题，"
    "不得虚构知识点、案例、公式、页码或参考文献。"
    "必须严格遵守用户指定的题型、数量、顺序和答案模式。"
    "不要使用 Markdown 表格。"
)

REPAIR_SYSTEM_PROMPT = (
    "你是课程自测题格式与质量修复助手。"
    "请根据校验问题、原始组卷蓝图和证据，修复整份试题。"
    "只能依据提供的证据，不得新增资料外知识。"
    "必须返回完整修复版，不要解释修复过程。"
)


def _safe_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _chunk_sort_key(chunk: dict[str, Any]) -> tuple[int, int, int]:
    metadata = chunk.get("metadata", {}) or {}
    return (
        _safe_int(metadata.get("page_number")),
        _safe_int(metadata.get("slide_number")),
        _safe_int(metadata.get("chunk_index")),
    )


def _sample_evenly(
    items: list[dict[str, Any]],
    limit: int,
) -> list[dict[str, Any]]:
    if limit <= 0:
        return []
    if len(items) <= limit:
        return list(items)
    if limit == 1:
        return [items[len(items) // 2]]

    last = len(items) - 1
    indexes = sorted(
        {
            round(index * last / (limit - 1))
            for index in range(limit)
        }
    )
    return [items[index] for index in indexes]


def _target_evidence_count(
    question_count: int,
    available_count: int,
) -> int:
    target = max(
        MIN_EVIDENCE_CHUNKS,
        question_count * CHUNKS_PER_QUESTION,
    )
    target = min(MAX_EVIDENCE_CHUNKS, target)
    return min(target, available_count)


def _representative_chunks(
    chunks: list[dict[str, Any]],
    question_count: int,
) -> list[dict[str, Any]]:
    target = _target_evidence_count(question_count, len(chunks))
    if target <= 0:
        return []

    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for chunk in chunks:
        metadata = chunk.get("metadata", {}) or {}
        source_path = str(
            metadata.get("source_path")
            or metadata.get("file_name")
            or "unknown"
        )
        grouped[source_path].append(chunk)

    file_count = max(len(grouped), 1)
    per_file_limit = min(
        target,
        max(
            3,
            (target + file_count - 1) // file_count + 2,
        ),
    )

    per_file_samples: list[list[dict[str, Any]]] = []
    for source_path in sorted(grouped):
        sorted_chunks = sorted(
            grouped[source_path],
            key=_chunk_sort_key,
        )
        per_file_samples.append(
            _sample_evenly(sorted_chunks, per_file_limit)
        )

    selected: list[dict[str, Any]] = []
    sample_index = 0

    while len(selected) < target:
        added = False
        for samples in per_file_samples:
            if sample_index < len(samples):
                selected.append(samples[sample_index])
                added = True
                if len(selected) >= target:
                    break
        if not added:
            break
        sample_index += 1

    if len(selected) < target:
        selected_ids = {id(chunk) for chunk in selected}
        fallback: list[dict[str, Any]] = []

        for source_path in sorted(grouped):
            fallback.extend(
                sorted(grouped[source_path], key=_chunk_sort_key)
            )

        for chunk in fallback:
            if id(chunk) in selected_ids:
                continue
            selected.append(chunk)
            if len(selected) >= target:
                break

    return selected



def _metadata_int(
    metadata: dict[str, Any],
    key: str,
) -> int | None:
    value = metadata.get(key)
    if value is None or str(value).strip() == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _normalized_source_key(
    metadata: dict[str, Any],
) -> str:
    source = str(
        metadata.get("source_path")
        or metadata.get("file_name")
        or "unknown"
    ).strip()
    return source.replace("\\", "/").casefold()


def _page_location(
    metadata: dict[str, Any],
) -> tuple[str, int] | None:
    file_type = str(
        metadata.get("file_type") or ""
    ).strip().lower().lstrip(".")

    slide_number = _metadata_int(
        metadata,
        "slide_number",
    )
    page_number = _metadata_int(
        metadata,
        "page_number",
    )

    if file_type in {"ppt", "pptx"}:
        if slide_number is not None:
            return "slide", slide_number
        return None

    if file_type == "pdf":
        if page_number is not None:
            return "page", page_number
        return None

    if slide_number is not None:
        return "slide", slide_number

    if page_number is not None:
        return "page", page_number

    return None


def _page_sort_key(
    page: dict[str, Any],
) -> tuple[str, int, int]:
    location_order = {
        "slide": 0,
        "page": 1,
        "chunk_fallback": 2,
    }
    return (
        str(page.get("source_key") or ""),
        location_order.get(
            str(page.get("location_type") or ""),
            9,
        ),
        _safe_int(page.get("location_number")),
    )


def _merge_page_text(
    chunks: list[dict[str, Any]],
) -> str:
    merged_parts: list[str] = []
    seen_texts: set[str] = set()
    current_length = 0

    for chunk in sorted(chunks, key=_chunk_sort_key):
        cleaned = clean_math_text(
            " ".join(
                str(chunk.get("text") or "").split()
            )
        ).strip()

        if not cleaned:
            continue

        duplicate_key = re.sub(
            r"\s+",
            "",
            cleaned,
        ).casefold()

        if duplicate_key in seen_texts:
            continue

        seen_texts.add(duplicate_key)

        remaining = MAX_CHARS_PER_PAGE - current_length
        if remaining <= 0:
            break

        part = cleaned[:remaining]
        merged_parts.append(part)
        current_length += len(part)

    return "\n".join(merged_parts)


def _build_page_units(
    chunks: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    grouped: dict[
        tuple[str, str, int],
        list[dict[str, Any]],
    ] = defaultdict(list)

    descriptors: dict[
        tuple[str, str, int],
        dict[str, Any],
    ] = {}

    for fallback_index, chunk in enumerate(
        chunks,
        start=1,
    ):
        metadata = chunk.get("metadata", {}) or {}
        source_key = _normalized_source_key(metadata)
        location = _page_location(metadata)

        if location is None:
            location_type = "chunk_fallback"
            location_number = fallback_index
            metadata_quality = "fallback"
        else:
            location_type, location_number = location
            metadata_quality = "exact"

        key = (
            source_key,
            location_type,
            location_number,
        )

        grouped[key].append(chunk)

        if key not in descriptors:
            descriptors[key] = {
                "source_key": source_key,
                "source_path": str(
                    metadata.get("source_path") or ""
                ),
                "file_name": str(
                    metadata.get("file_name") or ""
                ),
                "file_type": str(
                    metadata.get("file_type") or ""
                ),
                "location_type": location_type,
                "location_number": location_number,
                "metadata_quality": metadata_quality,
            }

    pages: list[dict[str, Any]] = []

    for key, page_chunks in grouped.items():
        descriptor = descriptors[key]
        sorted_chunks = sorted(
            page_chunks,
            key=_chunk_sort_key,
        )

        chunk_ids: list[str] = []

        for fallback_index, chunk in enumerate(
            sorted_chunks,
            start=1,
        ):
            metadata = chunk.get("metadata", {}) or {}
            chunk_id = (
                chunk.get("id")
                or metadata.get("chunk_id")
                or metadata.get("id")
                or (
                    f"chunk-"
                    f"{metadata.get('chunk_index', fallback_index)}"
                )
            )
            chunk_ids.append(str(chunk_id))

        location_type = descriptor["location_type"]
        location_number = descriptor["location_number"]

        pages.append(
            {
                "page_id": "",
                "page_key": (
                    f"{descriptor['source_key']}|"
                    f"{location_type}:{location_number}"
                ),
                **descriptor,
                "page_number": (
                    location_number
                    if location_type == "page"
                    else None
                ),
                "slide_number": (
                    location_number
                    if location_type == "slide"
                    else None
                ),
                "chunks": sorted_chunks,
                "chunk_ids": chunk_ids,
                "evidence_ids": [],
                "text": _merge_page_text(sorted_chunks),
            }
        )

    pages.sort(key=_page_sort_key)

    for index, page in enumerate(pages, start=1):
        page["page_id"] = f"P{index:03d}"

    return pages


def _representative_pages(
    pages: list[dict[str, Any]],
    target_count: int,
) -> list[dict[str, Any]]:
    if target_count <= 0 or not pages:
        return []

    target = min(target_count, len(pages))

    grouped: dict[
        str,
        list[dict[str, Any]],
    ] = defaultdict(list)

    for page in pages:
        grouped[
            str(
                page.get("source_key")
                or page.get("source_path")
                or page.get("file_name")
                or "unknown"
            )
        ].append(page)

    file_count = max(len(grouped), 1)
    per_file_limit = min(
        target,
        max(
            2,
            (target + file_count - 1) // file_count + 1,
        ),
    )

    per_file_samples: list[list[dict[str, Any]]] = []

    for source_key in sorted(grouped):
        sorted_pages = sorted(
            grouped[source_key],
            key=_page_sort_key,
        )
        per_file_samples.append(
            _sample_evenly(
                sorted_pages,
                per_file_limit,
            )
        )

    selected: list[dict[str, Any]] = []
    selected_keys: set[str] = set()
    sample_index = 0

    while len(selected) < target:
        added = False

        for samples in per_file_samples:
            if sample_index >= len(samples):
                continue

            page = samples[sample_index]
            page_key = str(page.get("page_key") or "")

            if page_key in selected_keys:
                continue

            selected.append(page)
            selected_keys.add(page_key)
            added = True

            if len(selected) >= target:
                break

        if not added:
            break

        sample_index += 1

    if len(selected) < target:
        for page in sorted(pages, key=_page_sort_key):
            page_key = str(page.get("page_key") or "")

            if page_key in selected_keys:
                continue

            selected.append(page)
            selected_keys.add(page_key)

            if len(selected) >= target:
                break

    return selected



def _assign_page_evidence(
    pages: list[dict[str, Any]],
) -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
    dict[str, str],
]:
    prepared_pages: list[dict[str, Any]] = []

    for page in pages:
        usable_chunks = [
            chunk
            for chunk in page.get("chunks", [])
            if str(chunk.get("text") or "").strip()
        ]

        if not usable_chunks:
            continue

        copied_page = dict(page)
        copied_page["chunks"] = sorted(
            usable_chunks,
            key=_chunk_sort_key,
        )
        prepared_pages.append(copied_page)

    if not prepared_pages:
        return [], [], {}

    total_available_chunks = sum(
        len(page["chunks"])
        for page in prepared_pages
    )
    evidence_budget = min(
        MAX_EVIDENCE_CHUNKS,
        total_available_chunks,
    )

    selected_by_page: list[list[dict[str, Any]]] = [
        [] for _ in prepared_pages
    ]

    chunk_offset = 0
    selected_count = 0

    while selected_count < evidence_budget:
        added = False

        for page_index, page in enumerate(prepared_pages):
            chunks = page["chunks"]

            if chunk_offset >= len(chunks):
                continue

            selected_by_page[page_index].append(
                chunks[chunk_offset]
            )
            selected_count += 1
            added = True

            if selected_count >= evidence_budget:
                break

        if not added:
            break

        chunk_offset += 1

    assigned_pages: list[dict[str, Any]] = []
    hits: list[dict[str, Any]] = []
    evidence_to_page: dict[str, str] = {}

    for page, selected_chunks in zip(
        prepared_pages,
        selected_by_page,
    ):
        if not selected_chunks:
            continue

        assigned_page = dict(page)
        assigned_page["chunks"] = selected_chunks
        assigned_page["text"] = _merge_page_text(
            selected_chunks
        )
        assigned_page["chunk_ids"] = []
        assigned_page["evidence_ids"] = []

        for chunk in selected_chunks:
            metadata = chunk.get("metadata", {}) or {}
            fallback_chunk_id = (
                f"chunk-"
                f"{metadata.get('chunk_index', len(hits) + 1)}"
            )
            chunk_id = (
                chunk.get("id")
                or metadata.get("chunk_id")
                or metadata.get("id")
                or fallback_chunk_id
            )

            assigned_page["chunk_ids"].append(
                str(chunk_id)
            )

            rank = len(hits) + 1
            hit = _hit_from_chunk(chunk, rank)
            evidence_id = f"E{rank}"

            hits.append(hit)
            assigned_page["evidence_ids"].append(
                evidence_id
            )
            evidence_to_page[evidence_id] = str(
                assigned_page["page_id"]
            )

        assigned_pages.append(assigned_page)

    return assigned_pages, hits, evidence_to_page


def _format_page_evidence(
    pages: list[dict[str, Any]],
    hits: list[dict[str, Any]],
) -> str:
    hit_by_id = {
        f"E{hit['rank']}": hit
        for hit in hits
    }
    blocks: list[str] = []

    for page in pages:
        source_label = (
            page.get("file_name")
            or page.get("source_path")
            or "未知来源"
        )
        location_type = str(
            page.get("location_type") or ""
        )
        location_number = page.get("location_number")

        if location_type == "slide":
            location = f"幻灯片 {location_number}"
        elif location_type == "page":
            location = f"页码 {location_number}"
        else:
            location = "页码信息缺失的独立片段"

        evidence_ids = [
            str(item)
            for item in page.get("evidence_ids", [])
        ]

        evidence_lines: list[str] = []

        for evidence_id in evidence_ids:
            hit = hit_by_id.get(evidence_id)
            if not hit:
                continue

            cleaned = clean_math_text(
                " ".join(
                    str(hit.get("text") or "").split()
                )
            ).strip()

            evidence_lines.append(
                f"{evidence_id}："
                f"{cleaned[:MAX_CHARS_PER_CHUNK]}"
            )

        blocks.append(
            f"{page['page_id']}｜{source_label}｜{location}"
            f"｜可用证据：{'、'.join(evidence_ids)}\n"
            + "\n".join(evidence_lines)
        )

    return "\n\n".join(blocks)

def _normalize_page_ids(value: Any) -> list[str]:
    if isinstance(value, list):
        raw_items = value
    elif value is None:
        raw_items = []
    else:
        raw_items = re.findall(
            r"P\s*\d+",
            str(value),
            flags=re.IGNORECASE,
        )

    normalized: list[str] = []

    for item in raw_items:
        match = re.search(
            r"P\s*(\d+)",
            str(item),
            flags=re.IGNORECASE,
        )

        if not match:
            continue

        page_id = f"P{int(match.group(1)):03d}"

        if page_id not in normalized:
            normalized.append(page_id)

    return normalized


def _page_topic(page: dict[str, Any]) -> str:
    text = clean_math_text(
        " ".join(
            str(page.get("text") or "").split()
        )
    )

    if not text:
        return "课程资料核心知识点"

    first_sentence = re.split(
        r"[。！？；\n]",
        text,
        maxsplit=1,
    )[0].strip()

    return (
        first_sentence[:48]
        or text[:48]
        or "课程资料核心知识点"
    )

def _hit_from_chunk(
    chunk: dict[str, Any],
    rank: int,
) -> dict[str, Any]:
    metadata = chunk.get("metadata", {}) or {}
    kept_metadata = {
        "source_path": metadata.get("source_path", ""),
        "file_name": metadata.get("file_name", ""),
        "file_type": metadata.get("file_type", ""),
        "page_number": metadata.get("page_number"),
        "slide_number": metadata.get("slide_number"),
        "chunk_index": metadata.get("chunk_index"),
    }

    return {
        "rank": rank,
        "source": (
            kept_metadata.get("source_path")
            or kept_metadata.get("file_name")
        ),
        "text": chunk.get("text", ""),
        "metadata": kept_metadata,
        "similarity": None,
        "hybrid_score": None,
        "keyword_score": None,
    }


def _format_evidence(
    hits: list[dict[str, Any]],
) -> str:
    blocks: list[str] = []

    for hit in hits:
        metadata = hit.get("metadata", {}) or {}
        source_label = (
            metadata.get("file_name")
            or metadata.get("source_path")
            or "未知来源"
        )

        page = metadata.get("page_number")
        slide = metadata.get("slide_number")
        chunk_index = metadata.get("chunk_index")

        location_parts: list[str] = []
        if page is not None:
            location_parts.append(f"页码 {page}")
        if slide is not None:
            location_parts.append(f"幻灯片 {slide}")
        if chunk_index is not None:
            location_parts.append(f"Chunk {chunk_index}")

        location = (
            "，".join(location_parts)
            if location_parts
            else "位置未知"
        )

        text = clean_math_text(
            " ".join(str(hit.get("text") or "").split())
        )

        blocks.append(
            f"E{hit['rank']}｜{source_label}｜{location}\n"
            f"{text[:MAX_CHARS_PER_CHUNK]}"
        )

    return "\n\n".join(blocks)


def _type_plan(
    type_configs: list[dict[str, Any]],
) -> tuple[str, int]:
    lines: list[str] = []
    total = 0

    for config in type_configs:
        question_type = str(config.get("type") or "")
        count = int(config.get("count") or 0)

        if question_type not in TYPE_LABELS or count <= 0:
            continue

        total += count
        lines.append(
            f"- {TYPE_LABELS[question_type]}"
            f"（{question_type}）：{count} 道"
        )

    return "\n".join(lines), total


def _expected_type_sequence(
    type_configs: list[dict[str, Any]],
) -> list[str]:
    sequence: list[str] = []

    for config in type_configs:
        question_type = str(config.get("type") or "")
        count = int(config.get("count") or 0)

        if question_type in TYPE_LABELS and count > 0:
            sequence.extend([question_type] * count)

    return sequence


def _difficulty_counts(
    total_questions: int,
) -> tuple[int, int, int]:
    if total_questions <= 1:
        return total_questions, 0, 0
    if total_questions == 2:
        return 1, 1, 0

    easy = max(1, round(total_questions * 0.3))
    hard = max(1, round(total_questions * 0.2))
    medium = total_questions - easy - hard

    if medium < 1:
        medium = 1
        if easy >= hard and easy > 1:
            easy -= 1
        elif hard > 1:
            hard -= 1

    return easy, medium, hard


def _call_llm(
    system_prompt: str,
    user_prompt: str,
    *,
    temperature: float,
    max_tokens: int,
    timeout: int,
    stage: str,
) -> str:
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
        content = clean_math_text(
            data["choices"][0]["message"]["content"].strip()
        )

        finish_reason = data["choices"][0].get(
            "finish_reason",
            "",
        )
        if finish_reason == "length":
            logger.warning(
                "Self-test LLM output reached token limit: "
                "stage=%s model=%s max_tokens=%d",
                stage,
                model,
                max_tokens,
            )

        return content
    except (KeyError, IndexError, ValueError) as exc:
        raise DeepSeekError(
            f"自测题{stage}响应异常"
            f"（{get_llm_provider_label()}）: {exc}"
        ) from exc


def _extract_json_payload(raw_text: str) -> Any:
    text = raw_text.strip()

    fence_match = re.fullmatch(
        r"```(?:json)?\s*(.*?)\s*```",
        text,
        flags=re.DOTALL | re.IGNORECASE,
    )
    if fence_match:
        text = fence_match.group(1).strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    object_start = text.find("{")
    object_end = text.rfind("}")
    if object_start >= 0 and object_end > object_start:
        try:
            return json.loads(
                text[object_start : object_end + 1]
            )
        except json.JSONDecodeError:
            pass

    array_start = text.find("[")
    array_end = text.rfind("]")
    if array_start >= 0 and array_end > array_start:
        return json.loads(
            text[array_start : array_end + 1]
        )

    raise ValueError("模型未返回可解析的 JSON 蓝图。")


def _normalize_evidence_ids(value: Any) -> list[str]:
    if isinstance(value, list):
        raw_items = value
    elif value is None:
        raw_items = []
    else:
        raw_items = re.findall(
            r"E\s*\d+",
            str(value),
            flags=re.IGNORECASE,
        )

    normalized: list[str] = []

    for item in raw_items:
        match = re.search(
            r"E\s*(\d+)",
            str(item),
            flags=re.IGNORECASE,
        )
        if not match:
            continue

        evidence_id = f"E{int(match.group(1))}"
        if evidence_id not in normalized:
            normalized.append(evidence_id)

    return normalized


def _parse_blueprint(
    raw_blueprint: str,
) -> list[dict[str, Any]]:
    payload = _extract_json_payload(raw_blueprint)

    if isinstance(payload, dict):
        questions = payload.get("questions")
    else:
        questions = payload

    if not isinstance(questions, list):
        raise ValueError("蓝图 JSON 缺少 questions 数组。")

    return [
        item
        for item in questions
        if isinstance(item, dict)
    ]


def _normalize_topic(value: Any) -> str:
    return re.sub(
        r"[\W_]+",
        "",
        str(value or "").lower(),
    )



def _normalize_string_list(
    value: Any,
    *,
    limit: int = 8,
) -> list[str]:
    if isinstance(value, list):
        raw_items = value
    elif value is None:
        raw_items = []
    else:
        raw_items = re.split(r"[、,，;；\n]+", str(value))

    normalized: list[str] = []

    for item in raw_items:
        cleaned = clean_math_text(
            " ".join(str(item or "").split())
        ).strip()

        if not cleaned:
            continue

        cleaned = cleaned[:80]

        if cleaned not in normalized:
            normalized.append(cleaned)

        if len(normalized) >= limit:
            break

    return normalized


def _parse_concept_cards(
    raw_text: str,
) -> list[dict[str, Any]]:
    payload = _extract_json_payload(raw_text)

    if isinstance(payload, dict):
        cards = payload.get("pages")
    else:
        cards = payload

    if not isinstance(cards, list):
        raise ValueError("页面概念 JSON 缺少 pages 数组。")

    return [
        item
        for item in cards
        if isinstance(item, dict)
    ]


def _validate_concept_cards(
    items: list[dict[str, Any]],
    valid_page_ids: set[str],
) -> tuple[dict[str, dict[str, Any]], list[str]]:
    cards: dict[str, dict[str, Any]] = {}
    issues: list[str] = []

    for item in items:
        page_ids = _normalize_page_ids(
            item.get("page_id")
            or item.get("page_ids")
        )

        if len(page_ids) != 1:
            issues.append("存在缺少唯一 page_id 的页面概念卡。")
            continue

        page_id = page_ids[0]

        if page_id not in valid_page_ids:
            issues.append(
                f"页面概念卡引用了无效页面：{page_id}。"
            )
            continue

        role = str(item.get("role") or "").strip().lower()
        concepts = _normalize_string_list(
            item.get("concepts"),
            limit=8,
        )
        key_facts = _normalize_string_list(
            item.get("key_facts")
            or item.get("keyFacts"),
            limit=6,
        )
        prerequisites = _normalize_string_list(
            item.get("prerequisites"),
            limit=5,
        )
        outcomes = _normalize_string_list(
            item.get("outcomes"),
            limit=5,
        )

        if role not in PAGE_CONCEPT_ROLES:
            issues.append(
                f"{page_id} 的 role 无效：{role or '空'}。"
            )
            continue

        if not concepts:
            issues.append(
                f"{page_id} 缺少有效 concepts。"
            )
            continue

        if not key_facts:
            issues.append(
                f"{page_id} 缺少有效 key_facts。"
            )
            continue

        cards[page_id] = {
            "page_id": page_id,
            "concepts": concepts,
            "role": role,
            "key_facts": key_facts,
            "prerequisites": prerequisites,
            "outcomes": outcomes,
        }

    missing_page_ids = sorted(valid_page_ids - set(cards))

    if missing_page_ids:
        issues.append(
            "以下页面没有有效概念卡："
            + "、".join(missing_page_ids)
            + "。"
        )

    return cards, issues


def _extract_page_concept_cards(
    pages: list[dict[str, Any]],
    evidence_text: str,
) -> tuple[dict[str, dict[str, Any]], list[str]]:
    valid_page_ids = {
        str(page["page_id"])
        for page in pages
        if page.get("metadata_quality") == "exact"
    }

    if len(valid_page_ids) < 2:
        return {}, ["具有明确页码的页面少于两个。"]

    prompt = (
        "请分析以下页面证据，并为每个页面提取概念卡。"
        "只分析给出的页面，不得创建新页面。\n\n"
        "只输出可解析 JSON，不要使用 Markdown 代码块：\n"
        "{\n"
        '  "pages": [\n'
        "    {\n"
        '      "page_id": "P001",\n'
        '      "concepts": ["核心概念"],\n'
        '      "role": "definition",\n'
        '      "key_facts": ["该页最重要的事实"],\n'
        '      "prerequisites": [],\n'
        '      "outcomes": []\n'
        "    }\n"
        "  ]\n"
        "}\n\n"
        "role 只能是 definition、condition、mechanism、"
        "process、comparison、example、application、conclusion。\n"
        "concepts 使用资料中的规范概念名称，避免泛化为"
        "“知识点”“课程内容”等无意义词。\n"
        "prerequisites 表示理解该页前需要的概念或条件；"
        "outcomes 表示该页导出的结果、作用或后续内容。\n\n"
        f"页面证据：\n{evidence_text}"
    )

    raw_cards = _call_llm(
        BLUEPRINT_SYSTEM_PROMPT,
        prompt,
        temperature=0.1,
        max_tokens=min(
            5000,
            max(1800, len(valid_page_ids) * 220),
        ),
        timeout=180,
        stage="页面概念提取",
    )

    try:
        parsed_cards = _parse_concept_cards(raw_cards)
        return _validate_concept_cards(
            parsed_cards,
            valid_page_ids,
        )
    except (ValueError, json.JSONDecodeError) as exc:
        return {}, [str(exc)]


def _normalized_phrase(value: str) -> str:
    return re.sub(
        r"[\W_]+",
        "",
        str(value or "").casefold(),
    )


def _phrase_overlap(
    first: list[str],
    second: list[str],
) -> list[str]:
    matches: list[str] = []

    for first_item in first:
        first_key = _normalized_phrase(first_item)

        if len(first_key) < 2:
            continue

        for second_item in second:
            second_key = _normalized_phrase(second_item)

            if len(second_key) < 2:
                continue

            if (
                first_key == second_key
                or (
                    min(len(first_key), len(second_key)) >= 3
                    and (
                        first_key in second_key
                        or second_key in first_key
                    )
                )
            ):
                preferred = (
                    first_item
                    if len(first_item) <= len(second_item)
                    else second_item
                )

                if preferred not in matches:
                    matches.append(preferred)

    return matches


def _relation_for_cards(
    first: dict[str, Any],
    second: dict[str, Any],
    *,
    same_source: bool,
    location_distance: int | None,
) -> tuple[str, list[str], int] | None:
    shared_concepts = _phrase_overlap(
        first.get("concepts", []),
        second.get("concepts", []),
    )
    forward_links = _phrase_overlap(
        first.get("outcomes", []),
        second.get("prerequisites", []),
    )
    backward_links = _phrase_overlap(
        second.get("outcomes", []),
        first.get("prerequisites", []),
    )
    linked_concepts = [
        *forward_links,
        *[
            item
            for item in backward_links
            if item not in forward_links
        ],
    ]

    if not shared_concepts and not linked_concepts:
        return None

    roles = frozenset({
        str(first.get("role") or ""),
        str(second.get("role") or ""),
    })

    if roles == frozenset({"process"}):
        relation_type = "process_sequence"
    else:
        relation_type = ROLE_RELATION_TYPES.get(roles)

    if not relation_type:
        relation_type = (
            "cause_effect"
            if linked_concepts
            else "theory_case"
        )

    score = (
        len(shared_concepts) * 5
        + len(linked_concepts) * 6
        + 2
    )

    if same_source:
        score += 1

    if (
        same_source
        and location_distance is not None
        and 0 < location_distance <= 3
    ):
        score += 1

    return (
        relation_type,
        shared_concepts or linked_concepts,
        score,
    )


def _fusion_group_evidence(
    page_ids: list[str],
    page_by_id: dict[str, dict[str, Any]],
) -> list[str]:
    evidence_ids: list[str] = []

    for page_id in page_ids:
        page = page_by_id[page_id]

        for evidence_id in page.get("evidence_ids", []):
            if evidence_id not in evidence_ids:
                evidence_ids.append(evidence_id)

    return evidence_ids


def _build_fusion_groups(
    pages: list[dict[str, Any]],
    concept_cards: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    exact_pages = [
        page
        for page in pages
        if (
            page.get("metadata_quality") == "exact"
            and page.get("page_id") in concept_cards
        )
    ]
    page_by_id = {
        str(page["page_id"]): page
        for page in exact_pages
    }
    pair_candidates: list[dict[str, Any]] = []

    for first_index, first_page in enumerate(exact_pages):
        for second_page in exact_pages[first_index + 1 :]:
            first_id = str(first_page["page_id"])
            second_id = str(second_page["page_id"])
            same_source = (
                first_page.get("source_key")
                == second_page.get("source_key")
            )
            distance: int | None = None

            if same_source:
                first_location = _safe_int(
                    first_page.get("location_number")
                )
                second_location = _safe_int(
                    second_page.get("location_number")
                )
                distance = abs(
                    first_location - second_location
                )

            relation = _relation_for_cards(
                concept_cards[first_id],
                concept_cards[second_id],
                same_source=same_source,
                location_distance=distance,
            )

            if relation is None:
                continue

            relation_type, shared_concepts, score = relation
            page_ids = [first_id, second_id]

            pair_candidates.append(
                {
                    "group_id": "",
                    "relation_type": relation_type,
                    "relation": RELATION_LABELS[relation_type],
                    "shared_concepts": shared_concepts[:5],
                    "page_ids": page_ids,
                    "evidence_ids": _fusion_group_evidence(
                        page_ids,
                        page_by_id,
                    ),
                    "score": score,
                }
            )

    pair_candidates.sort(
        key=lambda item: (
            -_safe_int(item.get("score")),
            tuple(item.get("page_ids", [])),
        )
    )

    groups: list[dict[str, Any]] = []
    seen_page_sets: set[tuple[str, ...]] = set()

    for candidate in pair_candidates:
        page_key = tuple(
            sorted(candidate["page_ids"])
        )

        if page_key in seen_page_sets:
            continue

        groups.append(candidate)
        seen_page_sets.add(page_key)

        if len(groups) >= MAX_FUSION_GROUPS:
            break

    for pair in pair_candidates[:6]:
        if len(groups) >= MAX_FUSION_GROUPS:
            break

        pair_ids = list(pair["page_ids"])
        best_third: tuple[int, str, list[str]] | None = None

        for third_page in exact_pages:
            third_id = str(third_page["page_id"])

            if third_id in pair_ids:
                continue

            total_score = 0
            combined_concepts = list(
                pair.get("shared_concepts", [])
            )
            valid_links = 0

            for pair_id in pair_ids:
                pair_page = page_by_id[pair_id]
                same_source = (
                    pair_page.get("source_key")
                    == third_page.get("source_key")
                )
                distance = None

                if same_source:
                    distance = abs(
                        _safe_int(
                            pair_page.get("location_number")
                        )
                        - _safe_int(
                            third_page.get("location_number")
                        )
                    )

                relation = _relation_for_cards(
                    concept_cards[pair_id],
                    concept_cards[third_id],
                    same_source=same_source,
                    location_distance=distance,
                )

                if relation is None:
                    continue

                _, shared, score = relation
                valid_links += 1
                total_score += score

                for concept in shared:
                    if concept not in combined_concepts:
                        combined_concepts.append(concept)

            if valid_links < 2:
                continue

            if (
                best_third is None
                or total_score > best_third[0]
            ):
                best_third = (
                    total_score,
                    third_id,
                    combined_concepts,
                )

        if best_third is None:
            continue

        _, third_id, combined_concepts = best_third
        page_ids = [*pair_ids, third_id]
        page_key = tuple(sorted(page_ids))

        if page_key in seen_page_sets:
            continue

        groups.append(
            {
                "group_id": "",
                "relation_type": pair["relation_type"],
                "relation": (
                    f"{pair['relation']}的三页综合关系"
                ),
                "shared_concepts": combined_concepts[:6],
                "page_ids": page_ids,
                "evidence_ids": _fusion_group_evidence(
                    page_ids,
                    page_by_id,
                ),
                "score": (
                    _safe_int(pair.get("score"))
                    + best_third[0]
                ),
            }
        )
        seen_page_sets.add(page_key)

    groups.sort(
        key=lambda item: (
            -_safe_int(item.get("score")),
            len(item.get("page_ids", [])),
            tuple(item.get("page_ids", [])),
        )
    )

    for index, group in enumerate(
        groups[:MAX_FUSION_GROUPS],
        start=1,
    ):
        group["group_id"] = f"G{index:03d}"

    return groups[:MAX_FUSION_GROUPS]


def _format_fusion_groups(
    groups: list[dict[str, Any]],
) -> str:
    blocks: list[str] = []

    for group in groups:
        blocks.append(
            f"{group['group_id']}｜"
            f"{group['relation_type']}｜"
            f"{group['relation']}｜"
            f"页面：{'、'.join(group['page_ids'])}｜"
            f"共同概念：{'、'.join(group['shared_concepts'])}｜"
            f"可用证据：{'、'.join(group['evidence_ids'])}"
        )

    return "\n".join(blocks)


def _fusion_group_lookup(
    groups: list[dict[str, Any]],
) -> dict[tuple[str, ...], dict[str, Any]]:
    return {
        tuple(sorted(group.get("page_ids", []))): group
        for group in groups
    }


def _should_use_fusion(
    question_type: str,
    question_index: int,
    *,
    fusion_group_count: int,
    used_fusion_count: int,
) -> bool:
    if fusion_group_count <= 0:
        return False

    if question_type == "fill":
        return False

    if question_type == "essay":
        return True

    if question_type == "choice":
        choice_quota = max(1, fusion_group_count // 2)
        return (
            question_index % 2 == 1
            and used_fusion_count < choice_quota
        )

    return False


def _validate_blueprint(
    items: list[dict[str, Any]],
    expected_types: list[str],
    valid_evidence_ids: set[str],
    valid_page_ids: set[str],
    evidence_to_page: dict[str, str],
    generation_mode: str,
    fusion_groups: list[dict[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    issues: list[str] = []
    normalized: list[dict[str, Any]] = []
    fusion_groups = fusion_groups or []
    fusion_lookup = _fusion_group_lookup(fusion_groups)

    if len(items) != len(expected_types):
        issues.append(
            f"蓝图题量应为 {len(expected_types)}，"
            f"实际为 {len(items)}。"
        )

    for index, expected_type in enumerate(
        expected_types,
        start=1,
    ):
        if index > len(items):
            break

        item = items[index - 1]
        question_type = TYPE_ALIASES.get(
            str(item.get("type") or "").strip(),
            "",
        )
        difficulty = DIFFICULTY_ALIASES.get(
            str(item.get("difficulty") or "").strip(),
            "",
        )
        item_mode = str(
            item.get("mode") or "single_page"
        ).strip()
        knowledge_point = str(
            item.get("knowledge_point")
            or item.get("knowledgePoint")
            or item.get("topic")
            or ""
        ).strip()
        objective = str(
            item.get("objective")
            or item.get("goal")
            or ""
        ).strip()
        relation = str(
            item.get("relation") or ""
        ).strip()
        relation_type = str(
            item.get("relation_type")
            or item.get("relationType")
            or ""
        ).strip()
        page_ids = _normalize_page_ids(
            item.get("page_ids")
            or item.get("pages")
            or item.get("page_id")
        )
        evidence_ids = _normalize_evidence_ids(
            item.get("evidence_ids")
            or item.get("evidence")
        )

        if question_type != expected_type:
            issues.append(
                f"第 {index} 题题型应为 "
                f"{TYPE_LABELS[expected_type]}，"
                f"实际为 {item.get('type')!r}。"
            )

        if difficulty not in {
            "easy",
            "medium",
            "hard",
        }:
            issues.append(
                f"第 {index} 题难度字段无效。"
            )

        if not knowledge_point:
            issues.append(
                f"第 {index} 题缺少考查知识点。"
            )

        if not objective:
            issues.append(
                f"第 {index} 题缺少考查目标。"
            )

        if item_mode not in {
            "single_page",
            "fusion",
        }:
            issues.append(
                f"第 {index} 题 mode 无效。"
            )
            item_mode = "single_page"

        if (
            generation_mode == "single_page"
            and item_mode != "single_page"
        ):
            issues.append(
                f"第 {index} 题不得使用 fusion。"
            )

        invalid_page_ids = [
            page_id
            for page_id in page_ids
            if page_id not in valid_page_ids
        ]

        if invalid_page_ids:
            issues.append(
                f"第 {index} 题引用了无效页面："
                + "、".join(invalid_page_ids)
                + "。"
            )

        invalid_evidence_ids = [
            evidence_id
            for evidence_id in evidence_ids
            if evidence_id not in valid_evidence_ids
        ]

        if invalid_evidence_ids:
            issues.append(
                f"第 {index} 题引用了无效证据编号："
                + "、".join(invalid_evidence_ids)
                + "。"
            )

        valid_item_pages = [
            page_id
            for page_id in page_ids
            if page_id in valid_page_ids
        ]
        valid_item_evidence = [
            evidence_id
            for evidence_id in evidence_ids
            if evidence_id in valid_evidence_ids
        ]
        mapped_pages = {
            evidence_to_page[evidence_id]
            for evidence_id in valid_item_evidence
            if evidence_id in evidence_to_page
        }

        if not valid_item_evidence:
            issues.append(
                f"第 {index} 题没有绑定有效证据编号。"
            )

        if item_mode == "single_page":
            if len(valid_item_pages) != 1:
                issues.append(
                    f"第 {index} 题必须且只能绑定一个页面。"
                )

            if (
                len(valid_item_pages) == 1
                and mapped_pages
                and mapped_pages != {valid_item_pages[0]}
            ):
                issues.append(
                    f"第 {index} 题的证据跨越了多个页面。"
                )

            relation = ""
            relation_type = ""

        else:
            if generation_mode != "fusion":
                issues.append(
                    f"第 {index} 题当前不能使用融合模式。"
                )

            if not 2 <= len(valid_item_pages) <= 3:
                issues.append(
                    f"第 {index} 题 fusion 必须绑定 2～3 个页面。"
                )

            group = fusion_lookup.get(
                tuple(sorted(valid_item_pages))
            )

            if group is None:
                issues.append(
                    f"第 {index} 题未匹配到经过校验的融合关系组。"
                )
            else:
                expected_relation_type = str(
                    group.get("relation_type") or ""
                )

                if (
                    relation_type
                    and relation_type != expected_relation_type
                ):
                    issues.append(
                        f"第 {index} 题 relation_type "
                        "与候选关系组不一致。"
                    )

                relation_type = expected_relation_type
                relation = str(
                    group.get("relation") or relation
                )

            if not relation:
                issues.append(
                    f"第 {index} 题缺少融合关系说明。"
                )

            if relation_type not in FUSION_RELATION_TYPES:
                issues.append(
                    f"第 {index} 题 relation_type 无效。"
                )

            if mapped_pages != set(valid_item_pages):
                issues.append(
                    f"第 {index} 题的证据没有覆盖全部融合页面。"
                )

        normalized.append(
            {
                "number": index,
                "type": (
                    question_type
                    if question_type in TYPE_LABELS
                    else expected_type
                ),
                "difficulty": (
                    difficulty
                    if difficulty in {
                        "easy",
                        "medium",
                        "hard",
                    }
                    else "medium"
                ),
                "mode": item_mode,
                "page_ids": valid_item_pages,
                "relation": relation,
                "relation_type": relation_type,
                "knowledge_point": (
                    knowledge_point
                    or f"第 {index} 题核心知识点"
                ),
                "objective": (
                    objective
                    or "考查对课程资料的理解"
                ),
                "evidence_ids": valid_item_evidence,
            }
        )

    if (
        generation_mode == "single_page"
        and len(valid_page_ids) >= len(expected_types)
    ):
        page_counts = Counter(
            item["page_ids"][0]
            for item in normalized
            if len(item["page_ids"]) == 1
        )
        repeated_pages = [
            page_id
            for page_id, count in page_counts.items()
            if count > 1
        ]

        if repeated_pages:
            issues.append(
                "可用页面数量充足时，蓝图不得重复使用页面。"
            )

    if (
        generation_mode == "fusion"
        and fusion_groups
        and any(
            question_type in {"choice", "essay"}
            for question_type in expected_types
        )
        and not any(
            item.get("mode") == "fusion"
            for item in normalized
        )
    ):
        issues.append(
            "存在可靠融合关系时，蓝图至少应包含一道融合题。"
        )

    topic_counts = Counter(
        _normalize_topic(item["knowledge_point"])
        for item in normalized
        if _normalize_topic(item["knowledge_point"])
    )
    repeated_topics = [
        topic
        for topic, count in topic_counts.items()
        if count > 2
    ]

    if repeated_topics:
        issues.append(
            "蓝图中存在同一知识点超过两题的情况。"
        )

    return normalized, issues

def _evidence_topic(hit: dict[str, Any]) -> str:
    text = clean_math_text(
        " ".join(str(hit.get("text") or "").split())
    )
    if not text:
        return "课程资料核心知识点"

    first_sentence = re.split(
        r"[。！？；\n]",
        text,
        maxsplit=1,
    )[0].strip()

    return (
        first_sentence[:48]
        or text[:48]
        or "课程资料核心知识点"
    )


def _fallback_blueprint(
    expected_types: list[str],
    pages: list[dict[str, Any]],
    *,
    generation_mode: str = "single_page",
    fusion_groups: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    easy_count, medium_count, hard_count = (
        _difficulty_counts(len(expected_types))
    )
    difficulty_sequence = (
        ["easy"] * easy_count
        + ["medium"] * medium_count
        + ["hard"] * hard_count
    )
    fusion_groups = fusion_groups or []
    blueprint: list[dict[str, Any]] = []
    single_page_index = 0
    fusion_group_index = 0
    used_fusion_count = 0

    for index, question_type in enumerate(
        expected_types,
        start=1,
    ):
        use_fusion = (
            generation_mode == "fusion"
            and _should_use_fusion(
                question_type,
                index,
                fusion_group_count=len(fusion_groups),
                used_fusion_count=used_fusion_count,
            )
        )

        if use_fusion:
            group = fusion_groups[
                fusion_group_index % len(fusion_groups)
            ]
            fusion_group_index += 1
            used_fusion_count += 1
            page_ids = list(group["page_ids"])
            evidence_ids = list(group["evidence_ids"])
            knowledge_point = (
                "、".join(group.get("shared_concepts", []))
                or group.get("relation")
                or "跨页面综合知识点"
            )
            mode = "fusion"
            relation = str(group.get("relation") or "")
            relation_type = str(
                group.get("relation_type") or ""
            )
            objective = (
                "考查对相关页面概念关系的综合理解与应用"
            )
        else:
            page = pages[
                single_page_index % len(pages)
            ]
            single_page_index += 1
            page_ids = [str(page["page_id"])]
            evidence_ids = list(
                page.get("evidence_ids", [])
            )
            knowledge_point = _page_topic(page)
            mode = "single_page"
            relation = ""
            relation_type = ""
            objective = (
                "考查对该页面核心内容的理解与应用"
            )

        blueprint.append(
            {
                "number": index,
                "type": question_type,
                "difficulty": difficulty_sequence[
                    min(
                        index - 1,
                        len(difficulty_sequence) - 1,
                    )
                ],
                "mode": mode,
                "page_ids": page_ids,
                "relation": relation,
                "relation_type": relation_type,
                "knowledge_point": knowledge_point,
                "objective": objective,
                "evidence_ids": evidence_ids,
            }
        )

    return blueprint

def _blueprint_json(
    blueprint: list[dict[str, Any]],
) -> str:
    return json.dumps(
        {"questions": blueprint},
        ensure_ascii=False,
        indent=2,
    )


QUESTION_HEADER_RE = re.compile(
    r"^###\s*第\s*(\d+)\s*题\s*[｜|]\s*"
    r"(选择题|填空题|简答/大题)\s*$",
    flags=re.MULTILINE,
)


def _extract_question_headers(
    text: str,
) -> list[tuple[int, str]]:
    headers: list[tuple[int, str]] = []

    for number_text, type_label in QUESTION_HEADER_RE.findall(
        text
    ):
        question_type = TYPE_ALIASES.get(type_label, "")
        headers.append((int(number_text), question_type))

    return headers


def _validate_header_sequence(
    text: str,
    expected_types: list[str],
    section_name: str,
) -> list[str]:
    issues: list[str] = []
    headers = _extract_question_headers(text)

    expected_headers = [
        (index, question_type)
        for index, question_type in enumerate(
            expected_types,
            start=1,
        )
    ]

    if headers != expected_headers:
        issues.append(
            f"{section_name}题目标题序列不符合要求："
            "每题必须使用“### 第N题｜题型”格式，"
            "且题量、题号、题型和顺序必须完全一致。"
        )

    return issues


def _question_stems(text: str) -> list[str]:
    matches = list(QUESTION_HEADER_RE.finditer(text))
    stems: list[str] = []

    for index, match in enumerate(matches):
        start = match.end()
        end = (
            matches[index + 1].start()
            if index + 1 < len(matches)
            else len(text)
        )
        body = text[start:end]

        lines = [
            line.strip()
            for line in body.splitlines()
            if line.strip()
            and not line.strip().startswith("【")
            and not line.strip().startswith("A.")
            and not line.strip().startswith("B.")
            and not line.strip().startswith("C.")
            and not line.strip().startswith("D.")
        ]

        if lines:
            stems.append(
                re.sub(r"\s+", "", lines[0]).lower()
            )

    return stems


def _analysis_section(
    answer: str,
    answer_mode: str,
) -> str:
    if answer_mode == "end":
        marker = "## 答案与解析"
        return (
            answer.split(marker, 1)[1]
            if marker in answer
            else ""
        )

    if answer_mode == "dual":
        marker = "## 解析版"
        return (
            answer.split(marker, 1)[1]
            if marker in answer
            else ""
        )

    return answer


def _validate_final_answer(
    answer: str,
    expected_types: list[str],
    answer_mode: str,
    valid_evidence_ids: set[str],
    blueprint: list[dict[str, Any]],
    evidence_to_page: dict[str, str],
) -> list[str]:
    issues: list[str] = []

    if answer_mode == "inline":
        if "## 解析版" not in answer:
            issues.append(
                "缺少固定标题“## 解析版”。"
            )

        section = (
            answer.split("## 解析版", 1)[1]
            if "## 解析版" in answer
            else answer
        )
        issues.extend(
            _validate_header_sequence(
                section,
                expected_types,
                "解析版",
            )
        )
        stem_source = section

    elif answer_mode == "end":
        if "## 解析版" not in answer:
            issues.append(
                "缺少固定标题“## 解析版”。"
            )

        if "## 答案与解析" not in answer:
            issues.append(
                "缺少固定标题“## 答案与解析”。"
            )

        question_part = (
            answer.split("## 解析版", 1)[1]
            if "## 解析版" in answer
            else answer
        )

        if "## 答案与解析" in question_part:
            question_part = question_part.split(
                "## 答案与解析",
                1,
            )[0]

        issues.extend(
            _validate_header_sequence(
                question_part,
                expected_types,
                "题目区",
            )
        )
        stem_source = question_part

    else:
        if "## 练习版" not in answer:
            issues.append(
                "缺少固定标题“## 练习版”。"
            )

        if "## 解析版" not in answer:
            issues.append(
                "缺少固定标题“## 解析版”。"
            )

        practice_part = ""
        analysis_part = ""

        if "## 练习版" in answer:
            practice_part = answer.split(
                "## 练习版",
                1,
            )[1]

        if "## 解析版" in practice_part:
            practice_part, analysis_part = (
                practice_part.split(
                    "## 解析版",
                    1,
                )
            )

        issues.extend(
            _validate_header_sequence(
                practice_part,
                expected_types,
                "练习版",
            )
        )
        issues.extend(
            _validate_header_sequence(
                analysis_part,
                expected_types,
                "解析版",
            )
        )
        stem_source = practice_part

    stems = _question_stems(stem_source)
    normalized_stems = [
        stem
        for stem in stems
        if stem
    ]

    if len(normalized_stems) != len(
        set(normalized_stems)
    ):
        issues.append("存在完全重复的题干。")

    analysis_text = _analysis_section(
        answer,
        answer_mode,
    )
    evidence_blocks = re.findall(
        r"〖依据〗\s*([^\n]+)",
        analysis_text,
    )

    if len(evidence_blocks) < len(expected_types):
        issues.append(
            "含〖依据〗的解析数量少于题目总数。"
        )

    invalid_references: set[str] = set()
    missing_reference_count = 0

    for index, block in enumerate(
        evidence_blocks[: len(expected_types)],
        start=1,
    ):
        evidence_ids = _normalize_evidence_ids(block)

        if not evidence_ids:
            missing_reference_count += 1
            continue

        for evidence_id in evidence_ids:
            if evidence_id not in valid_evidence_ids:
                invalid_references.add(evidence_id)

        if index > len(blueprint):
            continue

        blueprint_item = blueprint[index - 1]
        allowed_evidence_ids = set(
            blueprint_item.get("evidence_ids", [])
        )
        unexpected_ids = [
            evidence_id
            for evidence_id in evidence_ids
            if evidence_id not in allowed_evidence_ids
        ]

        if unexpected_ids:
            issues.append(
                f"第 {index} 题引用了未分配给该题的证据："
                + "、".join(unexpected_ids)
                + "。"
            )

        expected_pages = set(
            blueprint_item.get("page_ids", [])
        )
        referenced_pages = {
            evidence_to_page[evidence_id]
            for evidence_id in evidence_ids
            if evidence_id in evidence_to_page
        }

        if (
            blueprint_item.get("mode") == "single_page"
            and referenced_pages != expected_pages
        ):
            issues.append(
                f"第 {index} 题的正式依据不属于其唯一页面。"
            )

        if (
            blueprint_item.get("mode") == "fusion"
            and referenced_pages != expected_pages
        ):
            issues.append(
                f"第 {index} 题的正式依据没有覆盖全部融合页面。"
            )

    if missing_reference_count:
        issues.append(
            f"有 {missing_reference_count} 个〖依据〗"
            "未包含有效 E 编号。"
        )

    if invalid_references:
        issues.append(
            "正式题目引用了无效证据编号："
            + "、".join(sorted(invalid_references))
            + "。"
        )

    return issues

def _format_requirements(answer_mode: str) -> str:
    common = (
        "每道题必须用独立标题："
        "### 第N题｜选择题、### 第N题｜填空题"
        "或 ### 第N题｜简答/大题。\n"
        "题号必须从1连续排列，题型和顺序必须与蓝图完全一致。\n"
    )

    if answer_mode == "inline":
        return (
            common
            + "只输出一个“## 解析版”。"
            "每道题后立即给出【答案】【解析】【考点】【依据】。"
        )

    if answer_mode == "end":
        return (
            common
            + "先输出“## 解析版”及全部题目，"
            "之后输出“## 答案与解析”。"
            "答案区按题号逐题给出【答案】【解析】【考点】【依据】，"
            "答案区不要再次使用“### 第N题｜题型”标题。"
        )

    return (
        common
        + "先输出“## 练习版”，再输出“## 解析版”。"
        "两个版本都必须包含完整且完全一致的题目标题序列；"
        "练习版不得出现答案字段，解析版每题后必须给出"
        "【答案】【解析】【考点】【依据】。"
    )


def _final_max_tokens(
    total_questions: int,
    answer_mode: str,
) -> int:
    per_question = 780 if answer_mode == "dual" else 560
    return min(
        12000,
        max(4200, total_questions * per_question),
    )


def generate_self_test(
    subject_paths,
    *,
    source_filters: list[str] | None,
    type_configs: list[dict[str, Any]],
    answer_mode: str,
    generation_mode: str = "single_page",
) -> dict[str, Any]:
    type_plan, total_questions = _type_plan(type_configs)
    expected_types = _expected_type_sequence(
        type_configs
    )

    if total_questions <= 0:
        raise ValueError("至少需要选择一种题型。")

    if total_questions > 30:
        raise ValueError(
            "自测题总题量最多 30 题。"
        )

    chunks = collect_chunks_by_scope(
        subject_paths,
        source_filters=source_filters,
    )

    if not chunks:
        raise ValueError(
            "当前资料范围内没有可用知识库 Chunk。"
        )

    page_units = [
        page
        for page in _build_page_units(chunks)
        if str(page.get("text") or "").strip()
    ]

    if not page_units:
        raise ValueError(
            "当前资料范围内无法形成有效页面证据单元。"
        )

    requested_generation_mode = (
        generation_mode
        if generation_mode in {
            "single_page",
            "fusion",
        }
        else "single_page"
    )

    if requested_generation_mode == "fusion":
        target_page_count = min(
            len(page_units),
            max(
                total_questions,
                min(12, total_questions * 2),
            ),
        )
    else:
        target_page_count = min(
            len(page_units),
            total_questions,
        )

    selected_pages = _representative_pages(
        page_units,
        target_page_count,
    )
    selected_pages, hits, evidence_to_page = (
        _assign_page_evidence(selected_pages)
    )

    if not selected_pages or not hits:
        raise ValueError(
            "当前资料范围内没有可用于出题的页面证据。"
        )

    evidence_text = _format_page_evidence(
        selected_pages,
        hits,
    )
    valid_evidence_ids = set(
        evidence_to_page.keys()
    )
    valid_page_ids = {
        str(page["page_id"])
        for page in selected_pages
    }
    pipeline_notes: list[str] = []
    fusion_groups: list[dict[str, Any]] = []
    concept_cards: dict[str, dict[str, Any]] = {}
    effective_generation_mode = "single_page"

    if requested_generation_mode == "fusion":
        try:
            concept_cards, concept_issues = (
                _extract_page_concept_cards(
                    selected_pages,
                    evidence_text,
                )
            )
        except DeepSeekError as exc:
            concept_issues = [str(exc)]
            concept_cards = {}

        if concept_issues:
            logger.warning(
                "Self-test concept-card validation issues: %s",
                " | ".join(concept_issues),
            )

        if concept_cards:
            fusion_groups = _build_fusion_groups(
                selected_pages,
                concept_cards,
            )

        if fusion_groups:
            effective_generation_mode = "fusion"
            pipeline_notes.append(
                f"已识别 {len(fusion_groups)} 个可靠页面关系组。"
            )
        else:
            pipeline_notes.append(
                "未发现通过程序校验的页面关系，"
                "本次已安全降级为单页出题。"
            )

    mode_rule = ANSWER_MODE_RULES.get(
        answer_mode,
        ANSWER_MODE_RULES["inline"],
    )
    easy_count, medium_count, hard_count = (
        _difficulty_counts(total_questions)
    )

    if effective_generation_mode == "fusion":
        fusion_text = _format_fusion_groups(
            fusion_groups
        )
        generation_rules = (
            "当前组卷模式：fusion。\n"
            "可以生成 single_page 或 fusion 题。\n"
            "填空题以 single_page 为主；"
            "选择题可部分融合；简答/大题优先融合。\n"
            "fusion 题只能选择下面列出的真实关系组，"
            "必须使用该组的 2～3 个页面，"
            "并让每个页面至少贡献一个 E 编号。\n\n"
            f"可用融合关系组：\n{fusion_text}\n"
        )
        schema_mode = "fusion"
    else:
        generation_rules = (
            "当前组卷模式：single_page。\n"
            "一道题必须且只能绑定一个页面；"
            "同一页面内可以使用多个 E 编号。\n"
            "页面数量足够时，每道题优先使用不同页面。\n"
        )
        schema_mode = "single_page"

    blueprint_prompt = (
        "请根据下面的页面证据生成内部组卷蓝图。\n\n"
        f"题型和数量：\n{type_plan}\n\n"
        f"{generation_rules}\n"
        "难度数量建议："
        f"基础 {easy_count} 题，"
        f"中等 {medium_count} 题，"
        f"困难 {hard_count} 题。"
        "题目较少时可微调，但困难题不得超出资料范围。\n\n"
        "只允许输出以下 JSON 结构，"
        "不要输出 Markdown 代码块：\n"
        "{\n"
        '  "questions": [\n'
        "    {\n"
        '      "number": 1,\n'
        '      "type": "choice",\n'
        '      "difficulty": "easy",\n'
        f'      "mode": "{schema_mode}",\n'
        '      "page_ids": ["P001"],\n'
        '      "relation": "",\n'
        '      "relation_type": "",\n'
        '      "knowledge_point": "具体知识点",\n'
        '      "objective": "具体考查目标",\n'
        '      "evidence_ids": ["E1"]\n'
        "    }\n"
        "  ]\n"
        "}\n\n"
        "蓝图规则：\n"
        "1. questions 数量必须与题目总数完全一致。\n"
        "2. type 只能是 choice、fill、essay，"
        "并严格保持用户指定的题型顺序和数量。\n"
        "3. difficulty 只能是 easy、medium、hard。\n"
        "4. single_page 题的 page_ids 必须且只能有一个页面。\n"
        "5. fusion 题必须绑定一个已列出的关系组，"
        "page_ids 为该组的 2～3 个页面，"
        "relation_type 与关系组一致。\n"
        "6. evidence_ids 必须真实，并且只能来自 page_ids。\n"
        "7. fusion 题的 evidence_ids 必须覆盖每一个融合页面。\n"
        "8. 同一知识点原则上最多两题。\n\n"
        f"页面证据：\n{evidence_text}"
    )

    raw_blueprint = _call_llm(
        BLUEPRINT_SYSTEM_PROMPT,
        blueprint_prompt,
        temperature=0.15,
        max_tokens=min(
            6000,
            max(2000, total_questions * 210),
        ),
        timeout=180,
        stage="蓝图",
    )

    blueprint_fallback_used = False

    try:
        parsed_blueprint = _parse_blueprint(
            raw_blueprint
        )
        blueprint, blueprint_issues = (
            _validate_blueprint(
                parsed_blueprint,
                expected_types,
                valid_evidence_ids,
                valid_page_ids,
                evidence_to_page,
                effective_generation_mode,
                fusion_groups,
            )
        )
    except (ValueError, json.JSONDecodeError) as exc:
        blueprint = []
        blueprint_issues = [str(exc)]

    if blueprint_issues:
        logger.warning(
            "Self-test blueprint validation failed: %s",
            " | ".join(blueprint_issues),
        )
        blueprint = _fallback_blueprint(
            expected_types,
            selected_pages,
            generation_mode=effective_generation_mode,
            fusion_groups=fusion_groups,
        )
        blueprint_fallback_used = True
        pipeline_notes.append(
            "模型蓝图未通过页面级校验，"
            "已使用本地合法蓝图。"
        )

    blueprint_text = _blueprint_json(blueprint)
    format_requirements = _format_requirements(
        answer_mode
    )

    page_rules = (
        "1. 每题只能使用蓝图为该题指定的页面。\n"
        "2. 每题的〖依据〗只能引用该题 evidence_ids 中的 E 编号。\n"
        "3. single_page 题不得混入其他页面。\n"
        "4. fusion 题必须综合 page_ids 中的全部页面，"
        "并在〖依据〗中至少引用每页一个 E 编号。\n"
        "5. page_id 仅供内部约束，不必显示给用户。\n"
    )

    final_prompt = (
        "请根据内部组卷蓝图和页面证据生成正式自测题。\n\n"
        f"题型和数量：\n{type_plan}\n\n"
        f"答案模式：{answer_mode}\n{mode_rule}\n\n"
        f"机器可校验格式：\n{format_requirements}\n\n"
        f"页面级命题规则：\n{page_rules}\n"
        "通用命题规则：\n"
        "1. 只依据课程资料，答案必须从证据中得到。\n"
        "2. 不得虚构知识点、案例、公式、页码或参考文献。\n"
        "3. 严格遵守题型数量和顺序，不得增加题型。\n"
        "4. 选择题必须有 A-D 四个选项，且只有一个最佳答案。\n"
        "5. 填空题答案明确，存在可接受变体时在解析中说明。\n"
        "6. 简答/大题给出分点得分要点。\n"
        "7. 每题解析包含〖答案〗〖解析〗〖考点〗〖依据〗。\n"
        "8. 不使用 Markdown 表格。\n\n"
        f"内部组卷蓝图：\n{blueprint_text}\n\n"
        f"页面证据：\n{evidence_text}"
    )

    max_tokens = _final_max_tokens(
        total_questions,
        answer_mode,
    )
    answer = _call_llm(
        FINAL_SYSTEM_PROMPT,
        final_prompt,
        temperature=0.22,
        max_tokens=max_tokens,
        timeout=300,
        stage="正式出题",
    )

    final_issues = _validate_final_answer(
        answer,
        expected_types,
        answer_mode,
        valid_evidence_ids,
        blueprint,
        evidence_to_page,
    )

    repair_attempted = False
    repair_succeeded = False

    if final_issues:
        repair_attempted = True
        logger.warning(
            "Self-test final validation failed: %s",
            " | ".join(final_issues),
        )

        repair_prompt = (
            "下面这份自测题未通过程序校验，请完整修复。\n\n"
            "校验问题：\n- "
            + "\n- ".join(final_issues)
            + "\n\n"
            f"必须遵守的格式：\n{format_requirements}\n\n"
            f"题型和数量：\n{type_plan}\n\n"
            f"答案模式：{answer_mode}\n{mode_rule}\n\n"
            f"页面规则：\n{page_rules}\n"
            f"内部组卷蓝图：\n{blueprint_text}\n\n"
            f"页面证据：\n{evidence_text}\n\n"
            f"待修复原文：\n{answer}"
        )

        repaired_answer = _call_llm(
            REPAIR_SYSTEM_PROMPT,
            repair_prompt,
            temperature=0.1,
            max_tokens=max_tokens,
            timeout=300,
            stage="自动修复",
        )

        repaired_issues = _validate_final_answer(
            repaired_answer,
            expected_types,
            answer_mode,
            valid_evidence_ids,
            blueprint,
            evidence_to_page,
        )

        if not repaired_issues:
            answer = repaired_answer
            repair_succeeded = True
            pipeline_notes.append(
                "首次正式输出未通过页面级校验，"
                "已自动修复一次。"
            )
        else:
            logger.warning(
                "Self-test repaired output still invalid: %s",
                " | ".join(repaired_issues),
            )

            if len(repaired_issues) < len(final_issues):
                answer = repaired_answer
                final_issues = repaired_issues

            pipeline_notes.append(
                "已尝试自动修复一次，但仍存在少量问题："
                + "；".join(final_issues[:3])
            )

    unique_sources = {
        str(
            hit.get("metadata", {}).get("source_path")
            or hit.get("metadata", {}).get("file_name")
            or ""
        )
        for hit in hits
    }
    unique_sources.discard("")

    coverage_note = (
        f"本次覆盖 {len(unique_sources)} 份资料，"
        f"使用 {len(selected_pages)} 个页面证据单元、"
        f"{len(hits)} 个 Chunk。"
    )

    if len(page_units) < total_questions:
        pipeline_notes.append(
            "可用页面少于题目数量，"
            "部分页面已按均衡顺序复用。"
        )

    fallback_page_count = sum(
        1
        for page in selected_pages
        if page.get("metadata_quality") == "fallback"
    )

    if fallback_page_count:
        pipeline_notes.append(
            f"有 {fallback_page_count} 个证据单元缺少明确页码，"
            "已作为独立单页片段使用，未参与页面融合。"
        )

    fusion_question_count = sum(
        1
        for item in blueprint
        if item.get("mode") == "fusion"
    )

    if effective_generation_mode == "fusion":
        pipeline_notes.append(
            f"本次蓝图包含 {fusion_question_count} 道融合题。"
        )

    if blueprint_fallback_used and not repair_attempted:
        logger.info(
            "Self-test used page-level fallback blueprint "
            "without final repair."
        )

    if repair_succeeded:
        logger.info(
            "Self-test page-level automatic repair succeeded."
        )

    warning = " ".join(
        [coverage_note, *pipeline_notes]
    ).strip()

    return {
        "success": True,
        "answer": answer,
        "warning": warning or None,
        "hits": hits,
    }
