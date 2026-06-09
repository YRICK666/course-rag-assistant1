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


def _validate_blueprint(
    items: list[dict[str, Any]],
    expected_types: list[str],
    valid_evidence_ids: set[str],
) -> tuple[list[dict[str, Any]], list[str]]:
    issues: list[str] = []
    normalized: list[dict[str, Any]] = []

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

        if difficulty not in {"easy", "medium", "hard"}:
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

        if not evidence_ids:
            issues.append(
                f"第 {index} 题没有绑定证据编号。"
            )

        invalid_ids = [
            evidence_id
            for evidence_id in evidence_ids
            if evidence_id not in valid_evidence_ids
        ]
        if invalid_ids:
            issues.append(
                f"第 {index} 题引用了无效证据编号："
                + "、".join(invalid_ids)
                + "。"
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
                    if difficulty
                    in {"easy", "medium", "hard"}
                    else "medium"
                ),
                "knowledge_point": (
                    knowledge_point
                    or f"第 {index} 题核心知识点"
                ),
                "objective": (
                    objective
                    or "考查对课程资料的理解"
                ),
                "evidence_ids": [
                    evidence_id
                    for evidence_id in evidence_ids
                    if evidence_id in valid_evidence_ids
                ],
            }
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
    hits: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    easy_count, medium_count, hard_count = _difficulty_counts(
        len(expected_types)
    )
    difficulty_sequence = (
        ["easy"] * easy_count
        + ["medium"] * medium_count
        + ["hard"] * hard_count
    )

    blueprint: list[dict[str, Any]] = []

    for index, question_type in enumerate(
        expected_types,
        start=1,
    ):
        hit = hits[(index - 1) % len(hits)]
        evidence_id = f"E{hit['rank']}"

        blueprint.append(
            {
                "number": index,
                "type": question_type,
                "difficulty": difficulty_sequence[
                    min(index - 1, len(difficulty_sequence) - 1)
                ],
                "knowledge_point": _evidence_topic(hit),
                "objective": (
                    "考查对该资料片段核心内容的理解与应用"
                ),
                "evidence_ids": [evidence_id],
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
) -> list[str]:
    issues: list[str] = []

    if answer_mode == "inline":
        if "## 解析版" not in answer:
            issues.append("缺少固定标题“## 解析版”。")

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
            issues.append("缺少固定标题“## 解析版”。")
        if "## 答案与解析" not in answer:
            issues.append("缺少固定标题“## 答案与解析”。")

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
            issues.append("缺少固定标题“## 练习版”。")
        if "## 解析版" not in answer:
            issues.append("缺少固定标题“## 解析版”。")

        practice_part = ""
        analysis_part = ""

        if "## 练习版" in answer:
            practice_part = answer.split(
                "## 练习版",
                1,
            )[1]
            if "## 解析版" in practice_part:
                practice_part, analysis_part = (
                    practice_part.split("## 解析版", 1)
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
    if len(normalized_stems) != len(set(normalized_stems)):
        issues.append("存在完全重复的题干。")

    analysis_text = _analysis_section(answer, answer_mode)
    evidence_blocks = re.findall(
        r"【依据】\s*([^\n]+)",
        analysis_text,
    )

    if len(evidence_blocks) < len(expected_types):
        issues.append(
            "含【依据】的解析数量少于题目总数。"
        )

    invalid_references: set[str] = set()
    missing_reference_count = 0

    for block in evidence_blocks:
        evidence_ids = _normalize_evidence_ids(block)

        if not evidence_ids:
            missing_reference_count += 1

        for evidence_id in evidence_ids:
            if evidence_id not in valid_evidence_ids:
                invalid_references.add(evidence_id)

    if missing_reference_count:
        issues.append(
            f"有 {missing_reference_count} 个【依据】"
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
) -> dict[str, Any]:
    type_plan, total_questions = _type_plan(type_configs)
    expected_types = _expected_type_sequence(type_configs)

    if total_questions <= 0:
        raise ValueError("至少需要选择一种题型。")
    if total_questions > 30:
        raise ValueError("自测题总题量最多 30 题。")

    chunks = collect_chunks_by_scope(
        subject_paths,
        source_filters=source_filters,
    )
    if not chunks:
        raise ValueError(
            "当前资料范围内没有可用知识库 Chunk。"
        )

    selected_chunks = _representative_chunks(
        chunks,
        total_questions,
    )
    hits = [
        _hit_from_chunk(chunk, index + 1)
        for index, chunk in enumerate(selected_chunks)
    ]
    evidence_text = _format_evidence(hits)
    valid_evidence_ids = {
        f"E{hit['rank']}"
        for hit in hits
    }

    mode_rule = ANSWER_MODE_RULES.get(
        answer_mode,
        ANSWER_MODE_RULES["inline"],
    )

    easy_count, medium_count, hard_count = (
        _difficulty_counts(total_questions)
    )

    blueprint_prompt = (
        "请根据下面资料证据生成内部组卷蓝图。\n\n"
        f"题型和数量：\n{type_plan}\n\n"
        "难度数量建议："
        f"基础 {easy_count} 题，"
        f"中等 {medium_count} 题，"
        f"困难 {hard_count} 题。"
        "题目较少时可微调，但困难题不得超出资料范围。\n\n"
        "只允许输出以下 JSON 结构，不要输出 Markdown 代码块：\n"
        "{\n"
        '  "questions": [\n'
        "    {\n"
        '      "number": 1,\n'
        '      "type": "choice",\n'
        '      "difficulty": "easy",\n'
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
        "4. 同一知识点原则上最多两题。\n"
        "5. 尽量覆盖不同文件和不同位置。\n"
        "6. 每题至少绑定一个真实 E 编号。\n\n"
        f"资料证据：\n{evidence_text}"
    )

    raw_blueprint = _call_llm(
        BLUEPRINT_SYSTEM_PROMPT,
        blueprint_prompt,
        temperature=0.15,
        max_tokens=min(
            5000,
            max(1800, total_questions * 170),
        ),
        timeout=180,
        stage="蓝图",
    )

    pipeline_notes: list[str] = []
    blueprint_fallback_used = False

    try:
        parsed_blueprint = _parse_blueprint(raw_blueprint)
        blueprint, blueprint_issues = _validate_blueprint(
            parsed_blueprint,
            expected_types,
            valid_evidence_ids,
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
            hits,
        )
        blueprint_fallback_used = True
        pipeline_notes.append(
            "模型蓝图未通过结构校验，已使用本地降级蓝图。"
        )

    blueprint_text = _blueprint_json(blueprint)
    format_requirements = _format_requirements(answer_mode)

    final_prompt = (
        "请根据内部组卷蓝图和资料证据生成正式自测题。\n\n"
        f"题型和数量：\n{type_plan}\n\n"
        f"答案模式：{answer_mode}\n{mode_rule}\n\n"
        f"机器可校验格式：\n{format_requirements}\n\n"
        "命题规则：\n"
        "1. 只依据课程资料，答案必须从证据中得到。\n"
        "2. 不得虚构知识点、案例、公式、页码或参考文献。\n"
        "3. 严格遵守题型数量和顺序，不得增加题型。\n"
        "4. 选择题必须有 A-D 四个选项，且只有一个最佳答案。\n"
        "5. 填空题答案明确，存在可接受变体时在解析中说明。\n"
        "6. 简答/大题给出分点得分要点。\n"
        "7. 每题解析包含【答案】【解析】【考点】【依据】。\n"
        "8. 【依据】只能引用蓝图和证据中已有的 E 编号。\n"
        "9. 不使用 Markdown 表格。\n\n"
        f"内部组卷蓝图：\n{blueprint_text}\n\n"
        f"资料证据：\n{evidence_text}"
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
            f"内部组卷蓝图：\n{blueprint_text}\n\n"
            f"资料证据：\n{evidence_text}\n\n"
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
        )

        if not repaired_issues:
            answer = repaired_answer
            repair_succeeded = True
            pipeline_notes.append(
                "首次正式输出未通过格式校验，已自动修复一次。"
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
                "已尝试自动修复一次，但仍存在少量格式问题："
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
        f"使用 {len(hits)} 个代表性 Chunk。"
    )

    if len(selected_chunks) < MIN_EVIDENCE_CHUNKS:
        pipeline_notes.append(
            "当前资料范围内可用 Chunk 少于 12 个，"
            "已使用全部可用代表性内容。"
        )

    if blueprint_fallback_used and not repair_attempted:
        logger.info(
            "Self-test used fallback blueprint without final repair."
        )

    if repair_succeeded:
        logger.info(
            "Self-test automatic repair succeeded."
        )

    warning = " ".join([coverage_note, *pipeline_notes]).strip()

    return {
        "success": True,
        "answer": answer,
        "warning": warning or None,
        "hits": hits,
    }
