from __future__ import annotations

import os
import re
import textwrap
from collections import defaultdict
from functools import lru_cache
from pathlib import Path
from typing import Any

import chromadb
from dotenv import load_dotenv
from sentence_transformers import SentenceTransformer


PROJECT_ROOT = Path(__file__).resolve().parents[1]
MATERIALS_DIR = PROJECT_ROOT / "materials"
OUTPUTS_DIR = PROJECT_ROOT / "outputs"
CHROMA_DIR = OUTPUTS_DIR / "chroma_db"
HF_CACHE_DIR = OUTPUTS_DIR / "hf_cache"
COLLECTION_NAME = "course_materials"
DEFAULT_EMBEDDING_MODEL = "BAAI/bge-small-zh-v1.5"
DEFAULT_TOP_K = 8
BROAD_TOP_K = 50
DEFAULT_DISPLAY_SOURCES = 5
BROAD_DISPLAY_SOURCES = 20
DEFAULT_NEIGHBOR_WINDOW = 2

CHAPTER_NUMERALS = {
    1: ["1", "一"],
    2: ["2", "二"],
    3: ["3", "三"],
    4: ["4", "四"],
    5: ["5", "五"],
    6: ["6", "六"],
    7: ["7", "七"],
    8: ["8", "八"],
    9: ["9", "九"],
}

OVERVIEW_QUERY_TRIGGERS = [
    "主要讲了什么",
    "讲了什么",
    "主要内容",
    "内容是什么",
    "概述",
    "概要",
    "总结",
    "小结",
]

OVERVIEW_MARKERS = [
    "目录",
    "内容",
    "主要内容",
    "本章内容",
    "学习内容",
    "学习目标",
    "教学目标",
    "重点",
    "难点",
    "重点难点",
    "本章重点",
    "本章难点",
    "小结",
    "总结",
    "本章小结",
]

DEFINITION_QUERY_TRIGGERS = [
    "是什么",
    "定义",
    "概念",
    "形式定义",
    "含义",
    "组成",
    "包括哪些",
    "特点",
    "区别",
]

DEFINITION_MARKERS = [
    "定义为",
    "可以定义为",
    "称为",
    "是指",
    "包括",
    "形式化定义",
    "形式定义",
    "模型",
    "结构",
    "特点",
    "性质",
    "组成",
    "构成",
    "五元组",
    "四元组",
    "三元组",
    "二元组",
    "公式",
]

DEFINITION_PATTERNS = [
    re.compile(r"由.{0,60}(组成|构成)"),
    re.compile(r"(可|可以)?表示为"),
    re.compile(r"(记作|记为)"),
]

TERM_ALIASES = {
    "DFA": [
        "DFA",
        "确定的有穷状态自动机",
        "确定性有穷状态自动机",
        "确定的有限状态自动机",
        "确定性有限状态自动机",
        "Deterministic Finite Automaton",
    ],
    "NFA": [
        "NFA",
        "不确定的有穷状态自动机",
        "非确定的有穷状态自动机",
        "不确定性有穷状态自动机",
        "非确定性有穷状态自动机",
        "Non-deterministic Finite Automaton",
        "Nondeterministic Finite Automaton",
    ],
    "FA": [
        "FA",
        "有穷状态自动机",
        "有限状态自动机",
        "Finite Automaton",
    ],
    "ε-NFA": [
        "ε-NFA",
        "ϵ-NFA",
        "epsilon-NFA",
        "εNFA",
        "ϵNFA",
        "带空移动的不确定有穷状态自动机",
        "带空移动的有穷状态自动机",
        "带ε移动的不确定有穷状态自动机",
        "带ε移动的有穷状态自动机",
    ],
}

TERM_PRIORITY_ORDER = ["ε-NFA", "DFA", "NFA", "FA"]
RELATED_CONCEPTS = {
    "DFA": ["DFA", "FA"],
    "NFA": ["NFA", "FA"],
    "ε-NFA": ["ε-NFA", "NFA", "FA"],
    "FA": ["FA"],
}
CONFLICT_CONCEPTS = {
    "DFA": ["NFA", "ε-NFA"],
    "NFA": ["DFA", "ε-NFA"],
    "ε-NFA": ["DFA"],
    "FA": ["DFA", "NFA", "ε-NFA"],
}

load_dotenv(PROJECT_ROOT / ".env")


def get_embedding_model_name() -> str:
    return os.getenv("EMBEDDING_MODEL", DEFAULT_EMBEDDING_MODEL).strip() or DEFAULT_EMBEDDING_MODEL


def resolve_outputs_dir(outputs_dir: str | Path | None = None) -> Path:
    return Path(outputs_dir) if outputs_dir is not None else OUTPUTS_DIR


def resolve_chroma_dir(
    *,
    outputs_dir: str | Path | None = None,
    chroma_dir: str | Path | None = None,
) -> Path:
    if chroma_dir is not None:
        return Path(chroma_dir)
    return resolve_outputs_dir(outputs_dir) / "chroma_db"


def ensure_output_dirs(outputs_dir: str | Path | None = None, chroma_dir: str | Path | None = None) -> None:
    resolved_outputs_dir = resolve_outputs_dir(outputs_dir)
    resolved_chroma_dir = resolve_chroma_dir(outputs_dir=resolved_outputs_dir, chroma_dir=chroma_dir)
    resolved_outputs_dir.mkdir(parents=True, exist_ok=True)
    resolved_chroma_dir.mkdir(parents=True, exist_ok=True)
    HF_CACHE_DIR.mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=2)
def get_embedding_model(model_name: str | None = None) -> SentenceTransformer:
    ensure_output_dirs()
    selected_model = model_name or get_embedding_model_name()
    return SentenceTransformer(selected_model, cache_folder=str(HF_CACHE_DIR))


def encode_texts(texts: list[str], model_name: str | None = None, batch_size: int = 32) -> list[list[float]]:
    if not texts:
        return []

    model = get_embedding_model(model_name)
    vectors = model.encode(
        texts,
        batch_size=batch_size,
        normalize_embeddings=True,
        show_progress_bar=False,
    )
    return vectors.tolist()


def get_chroma_client(
    *,
    outputs_dir: str | Path | None = None,
    chroma_dir: str | Path | None = None,
) -> chromadb.PersistentClient:
    ensure_output_dirs(outputs_dir=outputs_dir, chroma_dir=chroma_dir)
    resolved_chroma_dir = resolve_chroma_dir(outputs_dir=outputs_dir, chroma_dir=chroma_dir)
    return chromadb.PersistentClient(path=str(resolved_chroma_dir))


def reset_chroma_client_cache(
    *,
    outputs_dir: str | Path | None = None,
    chroma_dir: str | Path | None = None,
) -> None:
    return None


def get_collection(
    create: bool = True,
    *,
    outputs_dir: str | Path | None = None,
    chroma_dir: str | Path | None = None,
):
    client = get_chroma_client(outputs_dir=outputs_dir, chroma_dir=chroma_dir)
    if create:
        return client.get_or_create_collection(
            name=COLLECTION_NAME,
            metadata={"hnsw:space": "cosine"},
        )
    return client.get_collection(name=COLLECTION_NAME)


def reset_collection(
    *,
    outputs_dir: str | Path | None = None,
    chroma_dir: str | Path | None = None,
):
    client = get_chroma_client(outputs_dir=outputs_dir, chroma_dir=chroma_dir)
    try:
        client.delete_collection(name=COLLECTION_NAME)
    except Exception:
        pass
    return client.get_or_create_collection(
        name=COLLECTION_NAME,
        metadata={"hnsw:space": "cosine"},
    )


def collection_count(
    *,
    outputs_dir: str | Path | None = None,
    chroma_dir: str | Path | None = None,
) -> int:
    try:
        return int(get_collection(create=False, outputs_dir=outputs_dir, chroma_dir=chroma_dir).count())
    except Exception:
        return 0


def clean_math_text(text: str) -> str:
    replacements = {
        r"\Sigma": "Σ",
        r"\sum": "Σ",
        r"\delta": "δ",
        r"\Delta": "Δ",
        r"\times": "×",
        r"\to": "→",
        r"\rightarrow": "→",
        r"\subseteq": "⊆",
        r"\subset": "⊂",
        r"\in": "∈",
        r"\epsilon": "ε",
        r"\varepsilon": "ε",
        r"\lambda": "λ",
        r"\emptyset": "∅",
        r"\left": "",
        r"\right": "",
        r"\mid": "|",
        r"\{": "{",
        r"\}": "}",
        r"\(": "",
        r"\)": "",
        r"\[": "",
        r"\]": "",
    }

    cleaned = text
    for source, target in replacements.items():
        cleaned = cleaned.replace(source, target)

    cleaned = re.sub(r"\b([A-Za-z])_\{?(\d+)\}?", r"\1\2", cleaned)
    cleaned = re.sub(r"\^\{([^{}]+)\}", r"^\1", cleaned)
    cleaned = re.sub(r"_\{([^{}]+)\}", r"_\1", cleaned)
    cleaned = cleaned.replace("$$", "").replace("$", "")
    cleaned = re.sub(r"\\([A-Za-z]+)", r"\1", cleaned)
    cleaned = re.sub(r"\\+", "", cleaned)
    return cleaned.strip()


def normalize_for_match(text: str) -> str:
    text = text.casefold()
    text = text.replace("ϵ", "ε")
    text = re.sub(r"\s+", " ", text)
    return text


def compact_for_match(text: str) -> str:
    return re.sub(r"[\s\-_/·]+", "", normalize_for_match(text))


def is_ascii_term(term: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z][A-Za-z0-9\-]*", term.strip()))


def contains_term(text: str, term: str) -> bool:
    term = term.strip()
    if not term:
        return False

    normalized_text = normalize_for_match(text)
    normalized_term = normalize_for_match(term)
    if is_ascii_term(term):
        pattern = rf"(?<![a-z0-9]){re.escape(normalized_term)}(?![a-z0-9])"
        return re.search(pattern, normalized_text) is not None

    return normalized_term in normalized_text or compact_for_match(term) in compact_for_match(text)


def is_definition_question(question: str) -> bool:
    normalized_question = normalize_for_match(question)
    return any(trigger in normalized_question for trigger in DEFINITION_QUERY_TRIGGERS)


def unique_preserve_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        normalized = compact_for_match(item)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        result.append(item)
    return result


def chapter_patterns(chapter: int) -> list[str]:
    numerals = CHAPTER_NUMERALS.get(chapter, [str(chapter)])
    patterns: list[str] = []
    for numeral in numerals:
        patterns.extend([f"第{numeral}章", f"{numeral}章"])
    return patterns


def detect_chapter_number(text: str) -> int | None:
    compact_text = compact_for_match(text)
    for chapter in range(1, 10):
        if any(compact_for_match(pattern) in compact_text for pattern in chapter_patterns(chapter)):
            return chapter
    return None


def is_chapter_overview_question(question: str, chapter: int | None = None) -> bool:
    if chapter is None:
        chapter = detect_chapter_number(question)
    if chapter is None:
        return False
    normalized_question = normalize_for_match(question)
    return any(trigger in normalized_question for trigger in OVERVIEW_QUERY_TRIGGERS)


def source_display_name(file_name: str) -> str:
    match = re.search(r"第[一二三四五六七八九1-9]章.*", file_name)
    if match:
        return match.group(0)
    return file_name


def source_matches(source_text: str, source_filter: str) -> bool:
    source_text = source_text or ""
    source_filter = source_filter or ""
    if not source_filter:
        return True
    return compact_for_match(source_filter) in compact_for_match(source_text)


def source_item_matches_filter(source_item: dict[str, str], source_filter: str) -> bool:
    source_filter = source_filter or ""
    if not source_filter:
        return True

    fields = [
        source_item.get("source_path", ""),
        source_item.get("file_name", ""),
        source_item.get("display_name", ""),
    ]
    if any(field == source_filter for field in fields):
        return True

    combined = " ".join(field for field in fields if field)
    return source_matches(combined, source_filter)


def source_matches_chapter(file_name: str, source_path: str, chapter: int) -> bool:
    combined = f"{file_name} {source_path}"
    return any(source_matches(combined, pattern) for pattern in chapter_patterns(chapter))


def aliases_for_concepts(concepts: list[str]) -> list[str]:
    aliases: list[str] = []
    for concept in concepts:
        aliases.extend(TERM_ALIASES.get(concept, []))
    return unique_preserve_order(aliases)


def detect_primary_concepts(question: str) -> list[str]:
    matched: list[str] = []
    for concept in ["ε-NFA", "DFA", "NFA"]:
        if any(contains_term(question, alias) for alias in TERM_ALIASES[concept]):
            matched.append(concept)

    if matched:
        return matched

    if any(contains_term(question, alias) for alias in TERM_ALIASES["FA"]):
        return ["FA"]

    return []


def related_concepts_for(primary_concepts: list[str]) -> list[str]:
    related: list[str] = []
    for concept in primary_concepts:
        related.extend(RELATED_CONCEPTS.get(concept, [concept]))
    return unique_preserve_order(related)


def conflict_concepts_for(primary_concepts: list[str]) -> list[str]:
    if not primary_concepts:
        return []

    conflicts: list[str] = []
    related = set(related_concepts_for(primary_concepts))
    for concept in primary_concepts:
        conflicts.extend(CONFLICT_CONCEPTS.get(concept, []))

    return [concept for concept in unique_preserve_order(conflicts) if concept not in related]


def expand_query_terms(question: str) -> list[str]:
    terms: list[str] = []
    normalized_question = normalize_for_match(question)
    primary_concepts = detect_primary_concepts(question)

    english_terms = re.findall(r"[εϵ]?-?[A-Za-z][A-Za-z0-9\-]*", question)
    terms.extend(term.strip("-") for term in english_terms if term.strip("-"))

    cleaned_question = question
    for trigger in DEFINITION_QUERY_TRIGGERS:
        cleaned_question = cleaned_question.replace(trigger, " ")
    cleaned_question = re.sub(r"[，。？！?、:：；;（）()\[\]【】\"'“”]", " ", cleaned_question)
    cleaned_question = re.sub(r"(请问|什么|哪些|如何|怎么|的|是|有|和|与)", " ", cleaned_question)
    terms.extend(part.strip() for part in re.split(r"\s+", cleaned_question) if len(part.strip()) >= 2)

    for concept in related_concepts_for(primary_concepts):
        terms.extend(TERM_ALIASES.get(concept, []))

    for aliases in TERM_ALIASES.values():
        if any(contains_term(normalized_question, alias) for alias in aliases):
            terms.extend(aliases)

    return unique_preserve_order(terms)


def build_expanded_query(question: str, terms: list[str]) -> str:
    if not terms:
        return question
    return f"{question}\n相关术语别名：{'；'.join(terms[:12])}"


def marker_score(text: str) -> float:
    normalized_text = normalize_for_match(text)
    score = 0.0
    for marker in DEFINITION_MARKERS:
        if marker in normalized_text:
            score += 0.2
    for pattern in DEFINITION_PATTERNS:
        if pattern.search(text):
            score += 0.25
    return min(1.0, score)


def term_score(text: str, terms: list[str]) -> float:
    if not terms:
        return 0.0

    score = 0.0
    for term in terms:
        if contains_term(text, term):
            score += 0.45 if is_ascii_term(term) else min(0.7, 0.18 + len(term) / 20)
    return min(1.0, score)


def target_term_metrics(text: str, primary_concepts: list[str]) -> dict[str, float]:
    if not primary_concepts:
        return {
            "target_score": 0.0,
            "related_score": 0.0,
            "conflict_score": 0.0,
        }

    related_concepts = related_concepts_for(primary_concepts)
    related_only = [concept for concept in related_concepts if concept not in primary_concepts]
    conflict_concepts = conflict_concepts_for(primary_concepts)

    return {
        "target_score": term_score(text, aliases_for_concepts(primary_concepts)),
        "related_score": term_score(text, aliases_for_concepts(related_only)),
        "conflict_score": term_score(text, aliases_for_concepts(conflict_concepts)),
    }


def proximity_score(text: str, terms: list[str]) -> float:
    if not terms:
        return 0.0

    normalized_text = normalize_for_match(text)
    marker_positions: list[int] = []
    for marker in DEFINITION_MARKERS:
        index = normalized_text.find(normalize_for_match(marker))
        if index >= 0:
            marker_positions.append(index)
    if not marker_positions:
        return 0.0

    for term in terms:
        normalized_term = normalize_for_match(term)
        term_index = normalized_text.find(normalized_term)
        if term_index < 0:
            continue
        if any(abs(term_index - marker_index) <= 120 for marker_index in marker_positions):
            return 1.0
    return 0.0


def keyword_score(
    text: str,
    terms: list[str],
    definition_question: bool,
    primary_concepts: list[str] | None = None,
) -> dict[str, float]:
    primary_concepts = primary_concepts or []
    term_match = term_score(text, terms)
    definition_match = marker_score(text)
    proximity_match = proximity_score(text, terms)
    target_metrics = target_term_metrics(text, primary_concepts)
    target_match = target_metrics["target_score"]
    related_match = target_metrics["related_score"]
    conflict_match = target_metrics["conflict_score"]

    if definition_question and primary_concepts:
        total = (
            0.40 * target_match
            + 0.14 * related_match
            + 0.22 * term_match
            + 0.18 * definition_match
            + 0.06 * proximity_match
        )
        conflict_penalty = 0.34 * conflict_match if target_match == 0 else 0.16 * conflict_match
        total -= conflict_penalty
    elif definition_question:
        total = 0.52 * term_match + 0.35 * definition_match + 0.13 * proximity_match
    else:
        total = 0.82 * term_match + 0.12 * definition_match + 0.06 * proximity_match

    return {
        "keyword_score": max(0.0, min(1.0, total)),
        "term_score": term_match,
        "definition_score": definition_match,
        "proximity_score": proximity_match,
        "target_score": target_match,
        "related_score": related_match,
        "conflict_score": conflict_match,
    }


def overview_score(text: str, metadata: dict[str, Any], overview_question: bool) -> float:
    if not overview_question:
        return 0.0

    normalized_text = normalize_for_match(text)
    score = 0.0
    for marker in OVERVIEW_MARKERS:
        if marker in normalized_text:
            score += 0.22

    slide_number = get_slide_number(metadata)
    if slide_number is not None:
        if slide_number <= 5:
            score += 0.28
        elif slide_number <= 10:
            score += 0.12

    return min(1.0, score)


def fetch_all_records(
    collection,
    batch_size: int = 1000,
    source_paths: set[str] | None = None,
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    total = collection.count()
    for offset in range(0, total, batch_size):
        batch = collection.get(
            limit=batch_size,
            offset=offset,
            include=["documents", "metadatas"],
        )
        ids = batch.get("ids", [])
        documents = batch.get("documents", [])
        metadatas = batch.get("metadatas", [])
        for index, record_id in enumerate(ids):
            metadata = metadatas[index] if index < len(metadatas) and metadatas[index] else {}
            if source_paths is not None and metadata.get("source_path") not in source_paths:
                continue
            records.append(
                {
                    "id": record_id,
                    "text": documents[index] if index < len(documents) else "",
                    "metadata": metadata,
                }
            )
    return records


def list_indexed_sources(
    *,
    outputs_dir: str | Path | None = None,
    chroma_dir: str | Path | None = None,
) -> list[dict[str, str]]:
    try:
        collection = get_collection(create=False, outputs_dir=outputs_dir, chroma_dir=chroma_dir)
    except Exception:
        return []

    sources: dict[str, dict[str, str]] = {}
    for record in fetch_all_records(collection):
        metadata = record.get("metadata", {})
        source_path = metadata.get("source_path")
        file_name = metadata.get("file_name")
        if not source_path or not file_name:
            continue
        sources[source_path] = {
            "source_path": source_path,
            "file_name": file_name,
            "display_name": source_display_name(file_name),
        }

    return sorted(
        sources.values(),
        key=lambda source: (
            detect_chapter_number(source["display_name"]) or 999,
            source["display_name"],
        ),
    )


def resolve_source_paths(
    collection,
    *,
    question: str,
    chapter: int | None = None,
    source: str | None = None,
    source_filters: list[str] | None = None,
    outputs_dir: str | Path | None = None,
    chroma_dir: str | Path | None = None,
) -> tuple[set[str] | None, str | None, int | None]:
    sources = list_indexed_sources(outputs_dir=outputs_dir, chroma_dir=chroma_dir)
    source_filters = [item for item in (source_filters or []) if item]
    explicit_filter = chapter is not None or bool(source) or bool(source_filters)
    detected_chapter = chapter if chapter is not None else detect_chapter_number(question)
    if detected_chapter is None and source:
        detected_chapter = detect_chapter_number(source)
    matched_paths: set[str] = set()
    label: str | None = None

    if source_filters:
        for source_filter in source_filters:
            if detected_chapter is None:
                detected_chapter = detect_chapter_number(source_filter)
            for item in sources:
                if source_item_matches_filter(item, source_filter):
                    matched_paths.add(item["source_path"])
        label = f"{len(source_filters)} 个资料"
    elif source:
        for item in sources:
            if source_item_matches_filter(item, source):
                matched_paths.add(item["source_path"])
        label = source
    elif detected_chapter is not None:
        for item in sources:
            if source_matches_chapter(item["file_name"], item["source_path"], detected_chapter):
                matched_paths.add(item["source_path"])
        label = f"第{detected_chapter}章"

    if matched_paths:
        return matched_paths, label, detected_chapter

    if explicit_filter:
        return set(), label, detected_chapter

    return None, None, detected_chapter


def format_source(metadata: dict[str, Any]) -> str:
    file_name = metadata.get("file_name") or "unknown"
    file_type = metadata.get("file_type") or ""

    if metadata.get("page_number"):
        location = f"第 {metadata['page_number']} 页"
    elif metadata.get("slide_number"):
        location = f"第 {metadata['slide_number']} 张幻灯片"
    elif metadata.get("paragraph_start") or metadata.get("paragraph_number"):
        start = metadata.get("paragraph_start") or metadata.get("paragraph_number")
        end = metadata.get("paragraph_end") or start
        if end != start:
            location = f"第 {start}-{end} 段"
        else:
            location = f"第 {start} 段"
    else:
        location = "位置未知"

    chunk_index = metadata.get("chunk_index")
    chunk_text = f"，chunk {chunk_index}" if chunk_index is not None else ""
    return f"{file_name}（{file_type}，{location}{chunk_text}）"


def get_paragraph_range(metadata: dict[str, Any]) -> tuple[int, int] | None:
    start = metadata.get("paragraph_start") or metadata.get("paragraph_number")
    end = metadata.get("paragraph_end") or start
    if start is None:
        return None

    try:
        start_int = int(start)
        end_int = int(end)
    except (TypeError, ValueError):
        return None

    return min(start_int, end_int), max(start_int, end_int)


def get_slide_number(metadata: dict[str, Any]) -> int | None:
    slide_number = metadata.get("slide_number")
    if slide_number is None:
        return None
    try:
        return int(slide_number)
    except (TypeError, ValueError):
        return None


def make_hit(
    *,
    rank: int,
    hit_id: str,
    document: str,
    metadata: dict[str, Any],
    distance: float | int | None = None,
    keyword_metrics: dict[str, float] | None = None,
    hybrid_score: float | None = None,
    is_neighbor: bool = False,
    parent_rank: int | None = None,
    display_source: bool | None = None,
) -> dict[str, Any]:
    similarity = None
    if isinstance(distance, (float, int)):
        similarity = max(0.0, min(1.0, 1.0 - float(distance)))

    return {
        "rank": rank,
        "id": hit_id,
        "text": document,
        "metadata": metadata,
        "distance": distance,
        "similarity": similarity,
        "keyword_score": (keyword_metrics or {}).get("keyword_score", 0.0),
        "overview_score": (keyword_metrics or {}).get("overview_score", 0.0),
        "term_score": (keyword_metrics or {}).get("term_score", 0.0),
        "definition_score": (keyword_metrics or {}).get("definition_score", 0.0),
        "proximity_score": (keyword_metrics or {}).get("proximity_score", 0.0),
        "target_score": (keyword_metrics or {}).get("target_score", 0.0),
        "related_score": (keyword_metrics or {}).get("related_score", 0.0),
        "conflict_score": (keyword_metrics or {}).get("conflict_score", 0.0),
        "hybrid_score": hybrid_score if hybrid_score is not None else similarity,
        "source": format_source(metadata),
        "is_neighbor": is_neighbor,
        "parent_rank": parent_rank,
        "citation_rank": parent_rank if is_neighbor else rank,
        "display_source": (not is_neighbor) if display_source is None else display_source,
    }


def add_neighbor_context(collection, hits: list[dict[str, Any]], neighbor_window: int) -> list[dict[str, Any]]:
    if neighbor_window <= 0:
        return hits

    expanded: list[dict[str, Any]] = []
    original_ids = {hit.get("id") for hit in hits if hit.get("id")}
    emitted_ids: set[str] = set()
    file_cache: dict[str, dict[str, Any]] = {}

    for hit in hits:
        if hit.get("id") in emitted_ids:
            continue

        expanded.append(hit)
        if hit.get("id"):
            emitted_ids.add(hit["id"])

        metadata = hit.get("metadata", {})
        file_type = metadata.get("file_type")
        if file_type not in {".docx", ".txt", ".pptx"}:
            continue

        paragraph_range = get_paragraph_range(metadata)
        slide_number = get_slide_number(metadata)
        source_path = metadata.get("source_path")
        if not source_path:
            continue
        if file_type in {".docx", ".txt"} and not paragraph_range:
            continue
        if file_type == ".pptx" and slide_number is None:
            continue

        if source_path not in file_cache:
            try:
                file_cache[source_path] = collection.get(
                    where={"source_path": source_path},
                    include=["documents", "metadatas"],
                )
            except Exception:
                file_cache[source_path] = {}

        file_chunks = file_cache[source_path]
        file_ids = file_chunks.get("ids", [])
        documents = file_chunks.get("documents", [])
        metadatas = file_chunks.get("metadatas", [])

        candidates: list[tuple[int, int, str, str, dict[str, Any]]] = []
        for index, candidate_metadata in enumerate(metadatas):
            candidate_id = file_ids[index] if index < len(file_ids) else ""
            if not candidate_id or candidate_id in original_ids or candidate_id in emitted_ids:
                continue

            candidate_metadata = candidate_metadata or {}
            if file_type == ".pptx":
                candidate_slide = get_slide_number(candidate_metadata)
                if candidate_slide is None:
                    continue
                if not slide_number - neighbor_window <= candidate_slide <= slide_number + neighbor_window:
                    continue
                candidate_start = candidate_slide
                candidate_end = candidate_slide
            else:
                candidate_range = get_paragraph_range(candidate_metadata)
                if not candidate_range or not paragraph_range:
                    continue
                target_start = paragraph_range[0] - neighbor_window
                target_end = paragraph_range[1] + neighbor_window
                candidate_start, candidate_end = candidate_range
                overlaps_target = candidate_start <= target_end and candidate_end >= target_start
                if not overlaps_target:
                    continue

            document = documents[index] if index < len(documents) else ""
            candidates.append((candidate_start, candidate_end, candidate_id, document, candidate_metadata))

        for _, _, candidate_id, document, candidate_metadata in sorted(candidates):
            emitted_ids.add(candidate_id)
            expanded.append(
                make_hit(
                    rank=0,
                    hit_id=candidate_id,
                    document=document,
                    metadata=candidate_metadata,
                    is_neighbor=True,
                    parent_rank=hit["rank"],
                    display_source=False,
                )
            )

    for hit in expanded:
        hit["source"] = format_source(hit.get("metadata", {}))
        hit["citation_rank"] = hit.get("parent_rank") if hit.get("is_neighbor") else hit.get("rank")

    return expanded


def display_hits(hits: list[dict[str, Any]], max_sources: int = DEFAULT_DISPLAY_SOURCES) -> list[dict[str, Any]]:
    return [hit for hit in hits if hit.get("display_source", True) and not hit.get("is_neighbor")][:max_sources]


def combine_scores(
    vector_similarity: float | None,
    metrics: dict[str, float],
    definition_question: bool,
) -> float:
    vector = vector_similarity or 0.0
    keyword = metrics.get("keyword_score", 0.0)
    overview = metrics.get("overview_score", 0.0)

    if definition_question:
        score = 0.55 * vector + 0.45 * keyword
        if metrics.get("target_score", 0.0) > 0:
            score += 0.12
        elif metrics.get("related_score", 0.0) > 0 and metrics.get("conflict_score", 0.0) == 0:
            score += 0.04
        if metrics.get("term_score", 0.0) > 0 and metrics.get("definition_score", 0.0) > 0:
            score += 0.08
        if metrics.get("proximity_score", 0.0) > 0:
            score += 0.05
        if metrics.get("conflict_score", 0.0) > 0 and metrics.get("target_score", 0.0) == 0:
            score -= 0.18
    else:
        score = 0.75 * vector + 0.25 * keyword

    if overview > 0:
        score += 0.24 * overview

    return max(0.0, min(1.0, score))


def retrieve(
    question: str,
    top_k: int = DEFAULT_TOP_K,
    broad_mode: bool = False,
    model_name: str | None = None,
    include_neighbors: bool = True,
    neighbor_window: int = DEFAULT_NEIGHBOR_WINDOW,
    chapter: int | None = None,
    source: str | None = None,
    source_filters: list[str] | None = None,
    outputs_dir: str | Path | None = None,
    chroma_dir: str | Path | None = None,
) -> list[dict[str, Any]]:
    question = question.strip()
    if not question:
        return []

    try:
        collection = get_collection(create=False, outputs_dir=outputs_dir, chroma_dir=chroma_dir)
    except Exception:
        return []

    if collection.count() == 0:
        return []

    source_paths, source_label, effective_chapter = resolve_source_paths(
        collection,
        question=question,
        chapter=chapter,
        source=source,
        source_filters=source_filters,
        outputs_dir=outputs_dir,
        chroma_dir=chroma_dir,
    )
    if source_paths == set():
        return []

    definition_question = is_definition_question(question)
    overview_question = is_chapter_overview_question(question, effective_chapter)
    primary_concepts = detect_primary_concepts(question)
    terms = expand_query_terms(question)
    if overview_question:
        terms.extend(OVERVIEW_MARKERS)
        if source_label:
            terms.append(source_label)
    terms = unique_preserve_order(terms)
    expanded_query = build_expanded_query(question, terms)
    total_count = collection.count()
    effective_top_k = BROAD_TOP_K if broad_mode else top_k
    vector_candidate_count = min(total_count, max(effective_top_k * 5, effective_top_k + 24, 100))

    query_embedding = encode_texts([expanded_query], model_name=model_name)[0]
    vector_rows: list[dict[str, Any]] = []

    query_source_paths = sorted(source_paths) if source_paths else [None]
    for source_path in query_source_paths:
        query_kwargs: dict[str, Any] = {
            "query_embeddings": [query_embedding],
            "n_results": vector_candidate_count,
            "include": ["documents", "metadatas", "distances"],
        }
        if source_path:
            query_kwargs["where"] = {"source_path": source_path}

        try:
            results = collection.query(**query_kwargs)
        except Exception:
            if source_path is None:
                raise
            continue

        documents = results.get("documents", [[]])[0]
        metadatas = results.get("metadatas", [[]])[0]
        distances = results.get("distances", [[]])[0]
        ids = results.get("ids", [[]])[0]

        for index, document in enumerate(documents):
            metadata = metadatas[index] if index < len(metadatas) and metadatas[index] else {}
            if source_paths is not None and metadata.get("source_path") not in source_paths:
                continue
            vector_rows.append(
                {
                    "id": ids[index] if index < len(ids) else "",
                    "text": document,
                    "metadata": metadata,
                    "distance": distances[index] if index < len(distances) else None,
                    "vector_rank": len(vector_rows) + 1,
                }
            )

    candidates: dict[str, dict[str, Any]] = {}
    for row in vector_rows:
        document = row["text"]
        distance = row["distance"]
        metadata = row["metadata"]
        hit_id = row["id"]
        metrics = keyword_score(document, terms, definition_question, primary_concepts)
        metrics["overview_score"] = overview_score(document, metadata, overview_question)
        vector_similarity = None
        if isinstance(distance, (float, int)):
            vector_similarity = max(0.0, min(1.0, 1.0 - float(distance)))
        candidates[hit_id] = {
            "id": hit_id,
            "text": document,
            "metadata": metadata,
            "distance": distance,
            "vector_similarity": vector_similarity,
            "metrics": metrics,
            "hybrid_score": combine_scores(vector_similarity, metrics, definition_question),
            "vector_rank": row["vector_rank"],
        }

    keyword_records: list[dict[str, Any]] = []
    if terms or definition_question or overview_question:
        for record in fetch_all_records(collection, source_paths=source_paths):
            metrics = keyword_score(record["text"], terms, definition_question, primary_concepts)
            metrics["overview_score"] = overview_score(record["text"], record.get("metadata", {}), overview_question)
            if overview_question:
                keep_record = metrics["overview_score"] > 0 or metrics["keyword_score"] > 0.12
            elif definition_question and primary_concepts:
                has_target_or_safe_related = metrics["target_score"] > 0 or (
                    metrics["related_score"] > 0 and metrics["conflict_score"] == 0
                )
                has_definition_signal = metrics["definition_score"] > 0 or metrics["proximity_score"] > 0
                keep_record = has_target_or_safe_related and has_definition_signal
            elif definition_question:
                keep_record = metrics["term_score"] > 0 and (
                    metrics["definition_score"] > 0 or metrics["proximity_score"] > 0
                )
            else:
                keep_record = metrics["keyword_score"] > 0.15

            if keep_record:
                keyword_records.append({**record, "metrics": metrics})

    keyword_limit = max(effective_top_k * 5, 40)
    keyword_records = sorted(
        keyword_records,
        key=lambda record: (
            record["metrics"]["overview_score"],
            record["metrics"]["keyword_score"],
            record["metrics"]["target_score"],
            record["metrics"]["definition_score"],
            -record["metrics"]["conflict_score"],
            record["metrics"]["term_score"],
        ),
        reverse=True,
    )[:keyword_limit]

    for record in keyword_records:
        hit_id = record["id"]
        metrics = record["metrics"]
        if hit_id in candidates:
            candidates[hit_id]["metrics"] = metrics
            candidates[hit_id]["hybrid_score"] = combine_scores(
                candidates[hit_id].get("vector_similarity"),
                metrics,
                definition_question,
            )
            continue

        hybrid_score = combine_scores(None, metrics, definition_question)
        candidates[hit_id] = {
            "id": hit_id,
            "text": record["text"],
            "metadata": record["metadata"],
            "distance": None,
            "vector_similarity": None,
            "metrics": metrics,
            "hybrid_score": hybrid_score,
            "vector_rank": 999999,
        }

    ranked_by_score = sorted(
        candidates.values(),
        key=lambda candidate: (
            candidate["hybrid_score"],
            candidate["metrics"]["overview_score"],
            candidate["metrics"]["target_score"],
            candidate["metrics"]["definition_score"],
            -candidate["metrics"]["conflict_score"],
            candidate["metrics"]["term_score"],
            -candidate["vector_rank"],
        ),
        reverse=True,
    )

    if broad_mode and effective_top_k > top_k:
        pool = ranked_by_score[:effective_top_k * 2]
        by_source: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for c in pool:
            sp = str(c.get("metadata", {}).get("source_path", "unknown"))
            by_source[sp].append(c)

        selected: list[dict[str, Any]] = []
        selected_ids: set[str] = set()
        source_lists = list(by_source.values())
        while len(selected) < effective_top_k and any(source_lists):
            for lst in source_lists:
                if not lst:
                    continue
                c = lst.pop(0)
                cid = str(c.get("id", ""))
                if cid in selected_ids:
                    continue
                selected.append(c)
                selected_ids.add(cid)
                if len(selected) >= effective_top_k:
                    break
        ranked_candidates = selected
        display_limit = BROAD_DISPLAY_SOURCES
    else:
        ranked_candidates = ranked_by_score[:top_k]
        display_limit = DEFAULT_DISPLAY_SOURCES

    hits = []
    for rank, candidate in enumerate(ranked_candidates, start=1):
        hits.append(
            make_hit(
                rank=rank,
                hit_id=candidate["id"],
                document=candidate["text"],
                metadata=candidate["metadata"],
                distance=candidate["distance"],
                keyword_metrics=candidate["metrics"],
                hybrid_score=candidate["hybrid_score"],
                display_source=rank <= display_limit,
            )
        )

    if include_neighbors:
        return add_neighbor_context(collection, hits, neighbor_window=neighbor_window)
    return hits


def make_local_summary(question: str, hits: list[dict[str, Any]], max_chars_per_hit: int = 320) -> str:
    if not hits:
        return "资料库中没有检索到足够相关的内容。请确认已经把资料放入 materials 并完成建库。"

    visible_hits = display_hits(hits)
    if not visible_hits:
        visible_hits = hits[:DEFAULT_DISPLAY_SOURCES]

    lines = [
        "未配置 DeepSeek API key，以下为本地检索结果的简要摘要：",
        "",
    ]
    for hit in visible_hits:
        snippet = clean_math_text(" ".join(hit["text"].split()))
        snippet = textwrap.shorten(snippet, width=max_chars_per_hit, placeholder="...")
        lines.append(f"[{hit['rank']}] {snippet}")

    lines.extend(
        [
            "",
            "请以来源片段为准；如需生成更自然的整合答案，请在 .env 中配置 DEEPSEEK_API_KEY。",
        ]
    )
    return "\n".join(lines)
