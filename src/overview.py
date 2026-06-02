from __future__ import annotations

import hashlib
import json
import re
import textwrap
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ai_settings import load_ai_settings
from llm_deepseek import (
    DeepSeekError,
    get_deepseek_api_key,
    get_deepseek_model,
    is_deepseek_configured,
    post_chat_completions,
)
from retriever import (
    OUTPUTS_DIR,
    clean_math_text,
    detect_chapter_number,
    fetch_all_records,
    format_source,
    get_collection,
    source_display_name,
    source_item_matches_filter,
    source_matches,
    source_matches_chapter,
)


AI_DISABLED_WARNING = "AI 已关闭，仍可查看资料来源和页面预览。"
CHUNKS_JSONL = OUTPUTS_DIR / "extracted_text" / "chunks.jsonl"
SUMMARY_CACHE_PATH = OUTPUTS_DIR / "chapter_summaries.json"
STUDY_GUIDE_CACHE_PATH = OUTPUTS_DIR / "study_guides.json"

CONTENT_MARKERS = [
    "目录",
    "内容",
    "主要内容",
    "本章内容",
    "学习目标",
    "教学目标",
    "课程目的",
    "基本要求",
]
IMPORTANT_MARKERS = ["重点", "难点", "重点难点", "本章重点", "本章难点", "注意"]
SUMMARY_MARKERS = ["小结", "总结", "本章小结", "回顾"]
CONCEPT_MARKERS = [
    "定义",
    "概念",
    "称为",
    "记作",
    "记为",
    "定义为",
    "可以定义为",
    "性质",
    "定理",
    "模型",
    "结构",
    "五元组",
    "四元组",
]
METHOD_MARKERS = ["方法", "步骤", "过程", "流程", "算法", "构造", "转换", "证明", "推导", "设计", "实现"]
CONFUSION_MARKERS = ["区别", "比较", "注意", "易错", "不能", "不是", "相同", "不同", "确定", "不确定"]


@dataclass
class OverviewResult:
    title: str
    content: str
    sources: list[dict[str, Any]]
    cache_key: str | None = None
    cached: bool = False
    warning: str | None = None


@dataclass
class StudyGuideResult:
    title: str
    content: str
    sources: list[dict[str, Any]]
    cache_key: str | None = None
    cached: bool = False
    warning: str | None = None


def resolve_overview_outputs_dir(outputs_dir: str | Path | None = None) -> Path:
    return Path(outputs_dir) if outputs_dir is not None else OUTPUTS_DIR


def resolve_chunks_jsonl(
    *,
    outputs_dir: str | Path | None = None,
    chunks_jsonl: str | Path | None = None,
) -> Path:
    if chunks_jsonl is not None:
        return Path(chunks_jsonl)
    return resolve_overview_outputs_dir(outputs_dir) / "extracted_text" / "chunks.jsonl"


def resolve_summary_cache_path(
    *,
    outputs_dir: str | Path | None = None,
    summary_cache_path: str | Path | None = None,
) -> Path:
    if summary_cache_path is not None:
        return Path(summary_cache_path)
    return resolve_overview_outputs_dir(outputs_dir) / "chapter_summaries.json"


def resolve_study_guide_cache_path(
    *,
    outputs_dir: str | Path | None = None,
    cache_path: str | Path | None = None,
) -> Path:
    if cache_path is not None:
        return Path(cache_path)
    return resolve_overview_outputs_dir(outputs_dir) / "study_guides.json"


def load_chunks(path: str | Path = CHUNKS_JSONL) -> list[dict[str, Any]]:
    path = Path(path)
    if not path.exists():
        return []

    records: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as file:
        for line in file:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if record.get("text"):
                record["metadata"] = record.get("metadata") or {}
                records.append(record)
    return records


def load_cache(path: str | Path = SUMMARY_CACHE_PATH) -> dict[str, Any]:
    path = Path(path)
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as file:
            data = json.load(file)
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def save_cache(cache: dict[str, Any], path: str | Path = SUMMARY_CACHE_PATH) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        json.dump(cache, file, ensure_ascii=False, indent=2)


def filter_chunks(
    records: list[dict[str, Any]],
    *,
    chapter: int | None = None,
    source: str | None = None,
    source_filters: list[str] | None = None,
) -> list[dict[str, Any]]:
    source_filters = [item for item in (source_filters or []) if item]
    filtered: list[dict[str, Any]] = []
    for record in records:
        metadata = record.get("metadata", {})
        file_name = metadata.get("file_name", "")
        source_path = metadata.get("source_path", "")
        combined_source = f"{file_name} {source_path}"
        source_item = {
            "source_path": source_path,
            "file_name": file_name,
            "display_name": source_display_name(file_name),
        }

        if not source_filters and chapter is not None and not source_matches_chapter(file_name, source_path, chapter):
            continue
        if source_filters and not any(source_item_matches_filter(source_item, source_filter) for source_filter in source_filters):
            continue
        if source and not source_matches(combined_source, source):
            continue
        filtered.append(record)
    return filtered


def record_position(record: dict[str, Any]) -> tuple[str, int, int]:
    metadata = record.get("metadata", {})
    source_path = str(metadata.get("source_path", ""))
    location = metadata.get("slide_number") or metadata.get("page_number") or metadata.get("paragraph_start") or 999999
    chunk_index = metadata.get("chunk_index") or 999999
    try:
        location_int = int(location)
    except (TypeError, ValueError):
        location_int = 999999
    try:
        chunk_int = int(chunk_index)
    except (TypeError, ValueError):
        chunk_int = 999999
    return source_path, location_int, chunk_int


def record_text(record: dict[str, Any], max_chars: int = 380) -> str:
    text = clean_math_text(" ".join(str(record.get("text", "")).split()))
    return textwrap.shorten(text, width=max_chars, placeholder="...")


def contains_any(text: str, markers: list[str]) -> bool:
    compact_text = re.sub(r"\s+", "", text)
    return any(marker in compact_text for marker in markers)


def add_unique(
    selected: list[dict[str, Any]],
    seen_ids: set[str],
    candidates: list[dict[str, Any]],
    limit: int,
) -> None:
    for record in candidates:
        record_id = str(record.get("id") or f"{record_position(record)}|{record.get('text', '')[:32]}")
        if record_id in seen_ids:
            continue
        selected.append(record)
        seen_ids.add(record_id)
        if len(selected) >= limit:
            return


def first_record_per_source(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    first_by_source: dict[str, dict[str, Any]] = {}
    for record in sorted(records, key=record_position):
        metadata = record.get("metadata", {})
        source_path = str(metadata.get("source_path") or metadata.get("file_name") or "")
        if source_path and source_path not in first_by_source:
            first_by_source[source_path] = record
    return list(first_by_source.values())


def select_representative_chunks(
    records: list[dict[str, Any]],
    *,
    max_records: int = 14,
) -> list[dict[str, Any]]:
    sorted_records = sorted(records, key=record_position)
    selected: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    add_unique(selected, seen_ids, first_record_per_source(sorted_records), max_records)

    marker_groups = [
        CONTENT_MARKERS,
        IMPORTANT_MARKERS,
        SUMMARY_MARKERS,
        CONCEPT_MARKERS,
    ]
    for markers in marker_groups:
        candidates = [record for record in sorted_records if contains_any(str(record.get("text", "")), markers)]
        add_unique(selected, seen_ids, candidates[:4], max_records)
        if len(selected) >= max_records:
            break

    if len(selected) < max_records:
        add_unique(selected, seen_ids, sorted_records[: max_records * 2], max_records)

    return selected[:max_records]


def infer_title(
    records: list[dict[str, Any]],
    *,
    chapter: int | None = None,
    source: str | None = None,
    source_filters: list[str] | None = None,
) -> str:
    if source_filters:
        if len(source_filters) == 1:
            return Path(source_filters[0]).stem
        return f"所选资料（{len(source_filters)} 个）"
    if not records:
        if chapter is not None:
            return f"第{chapter}章"
        if source:
            return source
        return "全部资料"

    names: list[str] = []
    for record in sorted(records, key=record_position):
        metadata = record.get("metadata", {})
        file_name = str(metadata.get("file_name") or metadata.get("source_path") or "")
        if not file_name:
            continue
        display = source_display_name(file_name)
        display = Path(display).stem
        if display not in names:
            names.append(display)

    if source and names:
        return names[0]
    if chapter is not None:
        for name in names:
            if detect_chapter_number(name) == chapter:
                return name
        return f"第{chapter}章"
    if len(names) == 1:
        return names[0]
    if names:
        return "全部资料"
    return source or "全部资料"


def records_signature(records: list[dict[str, Any]]) -> str:
    digest = hashlib.sha1()
    for record in sorted(records, key=record_position):
        digest.update(str(record.get("id", "")).encode("utf-8"))
        digest.update(b"\0")
    return digest.hexdigest()[:16]


def make_cache_key(
    *,
    title: str,
    chapter: int | None,
    source: str | None,
    source_filters: list[str] | None,
    generator: str,
    signature: str,
) -> str:
    raw = json.dumps(
        {
            "type": "chapter_overview",
            "title": title,
            "chapter": chapter,
            "source": source,
            "source_filters": source_filters or [],
            "generator": generator,
            "signature": signature,
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def make_study_guide_cache_key(
    *,
    subject_name: str,
    title: str,
    source_filters: list[str] | None,
    generator: str,
    signature: str,
) -> str:
    raw = json.dumps(
        {
            "type": "study_guide",
            "subject_name": subject_name,
            "title": title,
            "source_filters": source_filters or [],
            "generator": generator,
            "signature": signature,
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def build_sources(records: list[dict[str, Any]], max_sources: int = 10) -> list[dict[str, Any]]:
    sources: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, record in enumerate(records, start=1):
        metadata = record.get("metadata", {})
        label = format_source(metadata)
        source_path = str(metadata.get("source_path", ""))
        file_name = str(metadata.get("file_name") or source_path)
        page_number = metadata.get("page_number")
        slide_number = metadata.get("slide_number")
        key = f"{source_path}|{metadata.get('location')}|{metadata.get('chunk_index')}"
        if key in seen:
            continue
        sources.append(
            {
                "rank": str(len(sources) + 1),
                "label": label,
                "source_path": source_path,
                "file_name": file_name,
                "file_type": str(metadata.get("file_type") or Path(file_name).suffix),
                "page_number": page_number,
                "slide_number": slide_number,
                "location": str(metadata.get("location") or page_number or slide_number or ""),
                "text": record_text(record, max_chars=260),
            }
        )
        seen.add(key)
        if len(sources) >= max_sources:
            break
    return sources


def section_snippets(records: list[dict[str, Any]], markers: list[str], limit: int = 3) -> list[str]:
    snippets = []
    for record in sorted(records, key=record_position):
        text = str(record.get("text", ""))
        if contains_any(text, markers):
            snippet = record_text(record, max_chars=220)
            if snippet and snippet not in snippets:
                snippets.append(snippet)
        if len(snippets) >= limit:
            break
    return snippets


def extract_candidate_terms(records: list[dict[str, Any]], limit: int = 10) -> list[str]:
    terms: list[str] = []
    blocked = {
        "目录",
        "内容",
        "主要内容",
        "本章内容",
        "小结",
        "总结",
        "重点",
        "难点",
        "定义",
        "例",
    }

    for record in sorted(records, key=record_position):
        for raw_line in str(record.get("text", "")).splitlines():
            line = clean_math_text(raw_line.strip(" 　:-：；;、"))
            if not line or line in blocked:
                continue
            if re.fullmatch(r"[\d./:\-年月日\s]+", line):
                continue
            if len(line) > 28:
                continue
            if len(line) < 2:
                continue
            if any(marker in line for marker in CONCEPT_MARKERS) or re.search(r"[A-Z]{2,}|[A-Za-z]+-[A-Za-z]+", line):
                candidate = line
            elif len(terms) < 4 and not contains_any(line, CONTENT_MARKERS + IMPORTANT_MARKERS + SUMMARY_MARKERS):
                candidate = line
            else:
                continue
            if candidate not in terms:
                terms.append(candidate)
            if len(terms) >= limit:
                return terms
    return terms


def bullet_lines(items: list[str], fallback: str) -> list[str]:
    if not items:
        return [f"- {fallback}"]
    return [f"- {item}" for item in items]


def build_local_overview(title: str, selected_records: list[dict[str, Any]], sources: list[dict[str, str]]) -> str:
    main_items = section_snippets(selected_records, CONTENT_MARKERS, limit=3)
    concept_items = extract_candidate_terms(selected_records, limit=8)
    important_items = section_snippets(selected_records, IMPORTANT_MARKERS, limit=3)
    summary_items = section_snippets(selected_records, SUMMARY_MARKERS, limit=2)

    if not main_items:
        main_items = [record_text(record, max_chars=220) for record in selected_records[:3]]

    if not important_items and summary_items:
        important_items = summary_items

    source_lines = [f"- [{source['rank']}] {source['label']}" for source in sources]
    if not source_lines:
        source_lines = ["- 暂无可用来源。"]

    lines: list[str] = [
        f"### 资料范围",
        title,
        "",
        "### 主要内容",
        *bullet_lines(main_items, "资料中未检索到明确的主要内容页。"),
        "",
        "### 核心概念",
        *bullet_lines(concept_items, "资料中未检索到明确的概念列表。"),
        "",
        "### 重点难点",
        *bullet_lines(important_items, "资料中未检索到明确的重点难点页。"),
        "",
        "### 复习建议",
        "- 先按标题页和目录页梳理资料知识框架，再逐个核对定义、性质和构造方法。",
        "- 对资料中标注为重点、难点、小结的页面进行二次复习，并结合普通问答继续追问具体概念。",
        "",
        "### 来源",
        *source_lines,
    ]
    return clean_math_text("\n".join(lines))


def source_rank_for_record(record: dict[str, Any], sources: list[dict[str, str]]) -> str | None:
    label = format_source(record.get("metadata", {}))
    for source in sources:
        if source.get("label") == label:
            return source.get("rank")
    source_path = str(record.get("metadata", {}).get("source_path", ""))
    for source in sources:
        if source_path and source.get("source_path") == source_path:
            return source.get("rank")
    return None


def snippet_with_source(record: dict[str, Any], sources: list[dict[str, str]], max_chars: int = 220) -> str:
    snippet = record_text(record, max_chars=max_chars)
    rank = source_rank_for_record(record, sources)
    return f"{snippet} [{rank}]" if rank else snippet


def term_with_source(term: str, records: list[dict[str, Any]], sources: list[dict[str, str]]) -> str:
    for record in sorted(records, key=record_position):
        if term and term in str(record.get("text", "")):
            rank = source_rank_for_record(record, sources)
            return f"{term} [{rank}]" if rank else term
    return term


def section_records(records: list[dict[str, Any]], markers: list[str], limit: int = 4) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    seen: set[str] = set()
    for record in sorted(records, key=record_position):
        text = str(record.get("text", ""))
        if not contains_any(text, markers):
            continue
        record_id = str(record.get("id") or f"{record_position(record)}|{text[:32]}")
        if record_id in seen:
            continue
        selected.append(record)
        seen.add(record_id)
        if len(selected) >= limit:
            break
    return selected


def study_guide_bullets(
    records: list[dict[str, Any]],
    sources: list[dict[str, str]],
    fallback: str,
    limit: int = 4,
) -> list[str]:
    items = [snippet_with_source(record, sources) for record in records[:limit]]
    return bullet_lines(items, fallback)


def select_study_guide_chunks(records: list[dict[str, Any]], max_records: int = 22) -> list[dict[str, Any]]:
    selected = select_representative_chunks(records, max_records=max_records)
    seen_ids = {str(record.get("id") or f"{record_position(record)}|{record.get('text', '')[:32]}") for record in selected}
    for markers in (METHOD_MARKERS, CONFUSION_MARKERS):
        candidates = section_records(records, markers, limit=5)
        add_unique(selected, seen_ids, candidates, max_records)
        if len(selected) >= max_records:
            break
    return selected[:max_records]


def build_local_study_guide(title: str, selected_records: list[dict[str, Any]], sources: list[dict[str, str]]) -> str:
    topic_records = first_record_per_source(selected_records)[:3] or selected_records[:3]
    concept_terms = extract_candidate_terms(selected_records, limit=8)
    definition_records = section_records(selected_records, CONCEPT_MARKERS, limit=4)
    method_records = section_records(selected_records, METHOD_MARKERS, limit=4)
    confusion_records = section_records(selected_records, CONFUSION_MARKERS + IMPORTANT_MARKERS, limit=4)

    topic_items = [snippet_with_source(record, sources, max_chars=180) for record in topic_records]
    concept_items = [term_with_source(term, selected_records, sources) for term in concept_terms]
    source_lines = [f"- [{source['rank']}] {source['label']}" for source in sources]
    if not source_lines:
        source_lines = ["- 暂无可用来源。"]

    lines: list[str] = [
        "### 学习主题",
        *bullet_lines(topic_items, "资料中未检索到明确的学习主题。"),
        "",
        "### 核心概念",
        *bullet_lines(concept_items, "资料中未检索到明确的概念列表。"),
        "",
        "### 重点定义",
        *study_guide_bullets(definition_records, sources, "资料中未检索到明确的定义页。"),
        "",
        "### 重要方法或流程",
        *study_guide_bullets(method_records, sources, "资料中未检索到明确的方法或流程。"),
        "",
        "### 易混淆点",
        *study_guide_bullets(confusion_records, sources, "资料中未检索到明确的易混淆点；复习时可对照相近概念的定义、条件和适用范围。"),
        "",
        "### 复习建议",
        f"- 围绕“{title}”先整理主题框架，再回到来源片段核对定义、条件、步骤和例题。",
        "- 对带有“重点、难点、注意、区别”等标记的内容优先二次复习，并用问答功能追问不清楚的概念。",
        "- 本提纲只根据已检索到的资料片段生成，未覆盖的内容请以原始资料为准。",
        "",
        "### 来源资料",
        *source_lines,
    ]
    return clean_math_text("\n".join(lines))


def build_deepseek_context(selected_records: list[dict[str, Any]], sources: list[dict[str, str]]) -> str:
    context_parts: list[str] = []
    source_by_key: dict[str, str] = {}
    for source in sources:
        source_by_key[source["label"]] = source["rank"]

    for record in selected_records:
        metadata = record.get("metadata", {})
        label = format_source(metadata)
        rank = source_by_key.get(label)
        if rank is None:
            continue
        text = record_text(record, max_chars=900)
        context_parts.append(f"[{rank}] 来源：{label}\n{text}")
    return "\n\n".join(context_parts)


def generate_deepseek_overview(
    *,
    title: str,
    selected_records: list[dict[str, Any]],
    sources: list[dict[str, str]],
) -> str:
    api_key = get_deepseek_api_key()
    if not api_key:
        raise DeepSeekError("DEEPSEEK_API_KEY is not configured.")

    context = build_deepseek_context(selected_records, sources)
    if not context:
        return "资料库中没有检索到足够内容，无法生成资料概览。"

    system_prompt = (
        "你是这门课的课程助教，正在给学生做考前导读。"
        "只能根据提供的课程资料片段生成内容，不能补充课件没有的外部细节。"
        "输出要像一分钟备考大局观导读：帮学生抓主线、抓重点、知道先看什么，不像正式报告。"
        "不要逐条复述所有内容，不要写套话。"
        "第一段必须直接进入内容，不要“根据资料”“以下是”“本资料主要涵盖”等客套开头。"
        "如果某个栏目资料依据不足，要明确说“当前上传课件未包含足够信息”。"
        "控制在 400 到 500 字以内，不使用 Markdown 表格，不使用 # 或 ## 标题，只使用指定的 ### 栏目。"
        "少用加粗，最多加粗 1 到 2 个核心词。"
        "来源引用只在关键结论后自然标注，例如 [1]、[2]，不要每句话都标。"
        "数学符号使用 Unicode 普通字符，不要输出 LaTeX 反斜杠命令。"
    )
    user_prompt = (
        f"资料范围：{title}\n\n"
        f"课程资料片段：\n{context}\n\n"
        "请生成考前导读式资料概览，严格使用以下栏目：\n"
        "### 这份资料先抓什么\n"
        "用 2 到 3 句话讲清资料主线：解决什么问题、核心概念是哪些、复习时先抓什么。\n"
        "### 最值得背的点\n"
        "列 3 到 5 条核心要点，每条尽量短，像考前划重点，标注来源编号。\n"
        "### 容易卡住的地方\n"
        "指出 1 到 2 个容易混淆或容易复习跑偏的点；如果资料不足，写“暂无明显卡点”。\n"
        "### 建议怎么复习\n"
        "给一个具体复习顺序，不要空泛。"
    )

    payload = {
        "model": get_deepseek_model(),
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.2,
        "stream": False,
    }

    try:
        data = post_chat_completions(payload, timeout=90)
        return clean_math_text(data["choices"][0]["message"]["content"].strip())
    except (KeyError, IndexError, ValueError) as exc:
        raise DeepSeekError(f"Unexpected DeepSeek response: {exc}") from exc


def generate_deepseek_study_guide(
    *,
    title: str,
    selected_records: list[dict[str, Any]],
    sources: list[dict[str, str]],
) -> str:
    api_key = get_deepseek_api_key()
    if not api_key:
        raise DeepSeekError("DEEPSEEK_API_KEY is not configured.")

    context = build_deepseek_context(selected_records, sources)
    if not context:
        return "资料库中没有检索到足够内容，无法生成复习提纲。"

    system_prompt = (
        "你是这门课的助教，正在给学生整理一份照着执行的期末复习 checklist。"
        "只能根据提供的课程资料片段生成内容，不能补充课件没有的外部细节。"
        "输出要行动导向，像考前任务清单，不像论文大纲或百科总结。"
        "不要“根据资料”“以下是”“本提纲旨在”等客套开头。"
        "如果某个栏目资料依据不足，要明确说“当前上传课件未包含足够信息”，不要硬凑。"
        "控制在 500 到 600 字以内，不使用 Markdown 表格，不使用 # 或 ## 标题，只使用指定的 ### 栏目。"
        "少用加粗。来源引用只放在关键考点或定义后面，例如 [1]、[2]。"
        "不要出题，不要生成计算题、选择题、填空题或额外练习题；自测问题只能用于检查是否掌握资料中的内容。"
        "高频易混点、必须掌握的考点只能来自资料片段，不能凭常识扩展。"
        "数学符号使用 Unicode 普通字符，不要输出 LaTeX 反斜杠命令。"
    )
    user_prompt = (
        f"资料范围：{title}\n\n"
        f"课程资料片段：\n{context}\n\n"
        "请生成期末通关任务清单，严格使用以下栏目：\n"
        "### 先自测这几个问题\n"
        "用问题形式列出 3 到 5 个必须会的问题，例如“你能不能说清 A 和 B 的区别？”；只基于资料片段提问，不额外生成新题。\n"
        "### 必须掌握的考点\n"
        "列 3 到 5 个核心考点，每条短一点，只写资料片段能支持的内容。\n"
        "### 高频易混点\n"
        "列 2 到 3 个容易混的概念，用短句说明区别，不要表格；只能来自资料片段。\n"
        "### 通关顺序\n"
        "给一个可执行复习步骤，例如先看定义、再看例题、最后自己推一遍。"
    )

    payload = {
        "model": get_deepseek_model(),
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.2,
        "stream": False,
    }

    try:
        data = post_chat_completions(payload, timeout=90)
        return clean_math_text(data["choices"][0]["message"]["content"].strip())
    except (KeyError, IndexError, ValueError) as exc:
        raise DeepSeekError(f"Unexpected DeepSeek response: {exc}") from exc


def get_chapter_overview(
    *,
    question: str = "概括这一章主要内容",
    chapter: int | None = None,
    source: str | None = None,
    source_filters: list[str] | None = None,
    use_deepseek: bool = True,
    force_refresh: bool = False,
    outputs_dir: str | Path | None = None,
    chunks_jsonl: str | Path | None = None,
    summary_cache_path: str | Path | None = None,
) -> OverviewResult:
    resolved_chunks_jsonl = resolve_chunks_jsonl(outputs_dir=outputs_dir, chunks_jsonl=chunks_jsonl)
    resolved_summary_cache_path = resolve_summary_cache_path(
        outputs_dir=outputs_dir,
        summary_cache_path=summary_cache_path,
    )
    records = load_chunks(resolved_chunks_jsonl)
    if not records:
        return OverviewResult(
            title="资料概览",
            content="未找到已提取的课程资料。请先运行：python src\\ingest.py",
            sources=[],
        )

    detected_chapter = chapter
    source_filters = [item for item in (source_filters or []) if item]
    if source_filters:
        detected_chapter = None
    elif detected_chapter is None:
        detected_chapter = detect_chapter_number(question)
    if not source_filters and detected_chapter is None and source:
        detected_chapter = detect_chapter_number(source)

    filtered_records = filter_chunks(
        records,
        chapter=detected_chapter,
        source=source,
        source_filters=source_filters,
    )
    if not filtered_records and (detected_chapter is not None or source or source_filters):
        return OverviewResult(
            title=infer_title([], chapter=detected_chapter, source=source, source_filters=source_filters),
            content="没有在已索引资料中找到匹配的章节或资料范围。",
            sources=[],
        )
    if not filtered_records:
        filtered_records = records

    title = infer_title(filtered_records, chapter=detected_chapter, source=source, source_filters=source_filters)
    selected_records = select_representative_chunks(filtered_records)
    sources = build_sources(selected_records)
    signature = records_signature(filtered_records)
    ai_enabled = load_ai_settings()["enabled"]
    generator = f"deepseek:{get_deepseek_model()}" if use_deepseek and is_deepseek_configured() else "local"
    cache_key = make_cache_key(
        title=title,
        chapter=detected_chapter,
        source=source,
        source_filters=source_filters,
        generator=generator,
        signature=signature,
    )

    cache = load_cache(resolved_summary_cache_path)
    if not force_refresh and cache_key in cache:
        cached_item = cache.get(cache_key, {})
        return OverviewResult(
            title=str(cached_item.get("title") or title),
            content=str(cached_item.get("content") or ""),
            sources=list(cached_item.get("sources") or sources),
            cache_key=cache_key,
            cached=True,
            warning=AI_DISABLED_WARNING if use_deepseek and not ai_enabled else None,
        )

    warning: str | None = None
    if use_deepseek and not ai_enabled:
        warning = AI_DISABLED_WARNING
        content = build_local_overview(title, selected_records, sources)
    elif use_deepseek and is_deepseek_configured():
        try:
            content = generate_deepseek_overview(title=title, selected_records=selected_records, sources=sources)
        except DeepSeekError as exc:
            warning = f"DeepSeek 调用失败，已回退到本地资料概览：{exc}"
            content = build_local_overview(title, selected_records, sources)
            generator = "local"
            cache_key = make_cache_key(
                title=title,
                chapter=detected_chapter,
                source=source,
                source_filters=source_filters,
                generator=generator,
                signature=signature,
            )
    else:
        content = build_local_overview(title, selected_records, sources)

    cache[cache_key] = {
        "title": title,
        "content": content,
        "sources": sources,
        "chapter": detected_chapter,
        "source": source,
        "source_filters": source_filters,
        "generator": generator,
        "signature": signature,
    }
    save_cache(cache, resolved_summary_cache_path)

    return OverviewResult(
        title=title,
        content=content,
        sources=sources,
        cache_key=cache_key,
        cached=False,
        warning=warning,
    )


def get_study_guide(
    *,
    subject_name: str = "当前科目",
    source_filters: list[str] | None = None,
    use_deepseek: bool = True,
    force_refresh: bool = False,
    outputs_dir: str | Path | None = None,
    cache_path: str | Path | None = None,
) -> StudyGuideResult:
    resolved_outputs_dir = resolve_overview_outputs_dir(outputs_dir)
    resolved_cache_path = resolve_study_guide_cache_path(
        outputs_dir=resolved_outputs_dir,
        cache_path=cache_path,
    )
    source_filters = [item for item in (source_filters or []) if item]

    try:
        collection = get_collection(create=False, outputs_dir=resolved_outputs_dir)
    except Exception:
        return StudyGuideResult(
            title="复习提纲",
            content="当前科目知识库为空。请先上传资料并重建知识库。",
            sources=[],
        )

    source_paths = set(source_filters) if source_filters else None
    records = fetch_all_records(collection, source_paths=source_paths)
    if not records and source_filters:
        return StudyGuideResult(
            title=infer_title([], source_filters=source_filters),
            content="没有在已建库资料中找到当前范围的内容。",
            sources=[],
        )
    if not records:
        return StudyGuideResult(
            title="全部资料",
            content="当前科目知识库为空。请先上传资料并重建知识库。",
            sources=[],
        )

    title = infer_title(records, source_filters=source_filters or None)
    selected_records = select_study_guide_chunks(records)
    sources = build_sources(selected_records, max_sources=12)
    signature = records_signature(records)
    ai_enabled = load_ai_settings()["enabled"]
    generator = f"deepseek:{get_deepseek_model()}" if use_deepseek and is_deepseek_configured() else "local"
    cache_key = make_study_guide_cache_key(
        subject_name=subject_name,
        title=title,
        source_filters=source_filters,
        generator=generator,
        signature=signature,
    )

    cache = load_cache(resolved_cache_path)
    if not force_refresh and cache_key in cache:
        cached_item = cache.get(cache_key, {})
        return StudyGuideResult(
            title=str(cached_item.get("title") or title),
            content=str(cached_item.get("content") or ""),
            sources=list(cached_item.get("sources") or sources),
            cache_key=cache_key,
            cached=True,
            warning=AI_DISABLED_WARNING if use_deepseek and not ai_enabled else None,
        )

    warning: str | None = None
    if use_deepseek and not ai_enabled:
        warning = AI_DISABLED_WARNING
        content = build_local_study_guide(title, selected_records, sources)
    elif use_deepseek and is_deepseek_configured():
        try:
            content = generate_deepseek_study_guide(
                title=title,
                selected_records=selected_records,
                sources=sources,
            )
        except DeepSeekError as exc:
            warning = f"DeepSeek 调用失败，已回退到本地复习提纲：{exc}"
            content = build_local_study_guide(title, selected_records, sources)
            generator = "local"
            cache_key = make_study_guide_cache_key(
                subject_name=subject_name,
                title=title,
                source_filters=source_filters,
                generator=generator,
                signature=signature,
            )
    else:
        content = build_local_study_guide(title, selected_records, sources)

    cache[cache_key] = {
        "title": title,
        "content": content,
        "sources": sources,
        "subject_name": subject_name,
        "source_filters": source_filters,
        "generator": generator,
        "signature": signature,
    }
    save_cache(cache, resolved_cache_path)

    return StudyGuideResult(
        title=title,
        content=content,
        sources=sources,
        cache_key=cache_key,
        cached=False,
        warning=warning,
    )
