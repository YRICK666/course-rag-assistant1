from __future__ import annotations

import re
from pathlib import Path


SCOPE_ALL = "全部资料"
SCOPE_SINGLE = "单个资料"
SCOPE_MULTI = "多个资料"
SCOPE_GROUP = "按系统识别的分组选择"


def chinese_number_to_int(text: str) -> int | None:
    digits = {
        "零": 0,
        "〇": 0,
        "一": 1,
        "二": 2,
        "两": 2,
        "三": 3,
        "四": 4,
        "五": 5,
        "六": 6,
        "七": 7,
        "八": 8,
        "九": 9,
    }
    text = text.strip()
    if not text:
        return None
    if text.isdigit():
        return int(text)
    if text in digits:
        return digits[text]
    if text == "十":
        return 10
    if text.startswith("十"):
        tail = text[1:]
        if tail in digits:
            return 10 + digits[tail]
    if text.endswith("十"):
        head = text[:-1]
        if head in digits:
            return digits[head] * 10
    if "十" in text:
        head, tail = text.split("十", 1)
        if head in digits and tail in digits:
            return digits[head] * 10 + digits[tail]
    return None


def strip_dates_for_grouping(file_name: str) -> str:
    stem = Path(file_name).stem
    stem = re.sub(r"\b20\d{2}[-_./－—]\d{1,2}[-_./－—]\d{1,2}\b", " ", stem)
    stem = re.sub(r"\b\d{4}年\d{1,2}月\d{1,2}日\b", " ", stem)
    return re.sub(r"\s+", " ", stem).strip()


def bounded_group_number(raw: str, upper: int = 30) -> int | None:
    value = chinese_number_to_int(raw)
    if value is None:
        return None
    if 1 <= value <= upper:
        return value
    return None


def infer_material_group(file_name: str) -> dict:
    text = strip_dates_for_grouping(file_name)
    compact = re.sub(r"\s+", "", text.casefold())
    cn_num = "一二两三四五六七八九十〇零"

    for pattern in [
        rf"第?(\d{{1,2}}|[{cn_num}]{{1,3}})次?实验",
        rf"实验\s*(\d{{1,2}}|[{cn_num}]{{1,3}})",
        r"\blab\s*0?(\d{1,2})\b",
    ]:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            number = bounded_group_number(match.group(1))
            if number is not None:
                return {
                    "group_type": "lab",
                    "group_name": f"实验{number}",
                    "display_label": f"实验{number}",
                    "confidence": "high",
                }

    for pattern in [
        rf"第\s*(\d{{1,2}}|[{cn_num}]{{1,3}})\s*章",
        rf"(\d{{1,2}}|[{cn_num}]{{1,3}})\s*章",
    ]:
        match = re.search(pattern, text)
        if match:
            number = bounded_group_number(match.group(1))
            if number is not None:
                return {
                    "group_type": "chapter",
                    "group_name": f"第{number}章",
                    "display_label": f"第{number}章",
                    "confidence": "high",
                }

    section_match = re.search(
        r"(?<!\d)(0?[1-9]|[12]\d|30)\s*[-_.－—]\s*(?:\d{1,2}\s*)?(?=[\u4e00-\u9fffA-Za-z])",
        text,
    )
    if section_match:
        number = bounded_group_number(section_match.group(1))
        if number is not None:
            return {
                "group_type": "chapter",
                "group_name": f"第{number}章",
                "display_label": f"第{number}章",
                "confidence": "medium",
            }

    for pattern in [
        rf"第\s*(\d{{1,2}}|[{cn_num}]{{1,3}})\s*讲",
        rf"(?<!\d)(\d{{1,2}}|[{cn_num}]{{1,3}})\s*讲",
        r"\blecture\s*0?(\d{1,2})\b",
    ]:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            number = bounded_group_number(match.group(1))
            if number is not None:
                return {
                    "group_type": "lecture",
                    "group_name": f"第{number}讲",
                    "display_label": f"第{number}讲",
                    "confidence": "high",
                }

    if any(keyword in compact for keyword in ("总复习", "复习")):
        return {
            "group_type": "review",
            "group_name": "复习资料",
            "display_label": "复习资料",
            "confidence": "high",
        }
    if any(keyword in compact for keyword in ("习题", "作业")):
        return {
            "group_type": "exercise",
            "group_name": "习题资料",
            "display_label": "习题资料",
            "confidence": "high",
        }
    if any(keyword in compact for keyword in ("课程介绍", "课程说明", "教学大纲", "绪论", "导论", "说明", "syllabus")):
        return {
            "group_type": "intro",
            "group_name": "课程介绍",
            "display_label": "课程介绍",
            "confidence": "medium",
        }
    return {
        "group_type": "other",
        "group_name": "其他资料",
        "display_label": "其他资料",
        "confidence": "low",
    }


def infer_material_category(file_name: str) -> str:
    return infer_material_group(file_name)["display_label"]


def grouped_materials(materials: list[dict]) -> dict[str, list[str]]:
    groups: dict[str, list[str]] = {}
    for material in materials:
        group = infer_material_category(material["relative_path"])
        groups.setdefault(group, []).append(material["relative_path"])
    return dict(sorted(groups.items(), key=lambda item: (0 if item[0].startswith("第") else 1, item[0])))


def indexed_source_path_set(indexed_sources: list[dict[str, str]]) -> set[str]:
    return {source["source_path"] for source in indexed_sources if source.get("source_path")}


def is_material_indexed(material: dict, indexed_paths: set[str]) -> bool:
    return material.get("file_type") != ".ppt" and material.get("relative_path") in indexed_paths


def material_build_status(material: dict, indexed_paths: set[str]) -> tuple[str, str]:
    relative_path = material["relative_path"]
    file_type = material["file_type"]
    if file_type == ".ppt":
        return "待处理", "pending"
    if relative_path in indexed_paths:
        return "已建库", "indexed"
    return "已上传未建库", "unindexed"


def resolve_scope_selection(
    materials: list[dict],
    *,
    mode: str,
    single_source: str = "",
    multi_sources: list[str] | None = None,
    source_groups: list[str] | None = None,
) -> tuple[str, list[str], str]:
    available = [material["relative_path"] for material in materials]
    available_set = set(available)

    if mode == SCOPE_SINGLE:
        sources = [single_source] if single_source in available_set else []
        label = "当前范围：1 个资料" if sources else "当前范围：未选择资料"
        return label, sources, mode

    if mode == SCOPE_MULTI:
        sources = [source for source in (multi_sources or []) if source in available_set]
        label = f"当前范围：{len(sources)} 个资料" if sources else "当前范围：未选择资料"
        return label, sources, mode

    if mode == SCOPE_GROUP:
        groups = grouped_materials(materials)
        selected_groups = [group for group in (source_groups or []) if group in groups]
        sources: list[str] = []
        for group in selected_groups:
            for source in groups.get(group, []):
                if source not in sources:
                    sources.append(source)
        if selected_groups:
            label = f"当前范围：{len(selected_groups)} 个分组，{len(sources)} 个资料"
        else:
            label = "当前范围：未选择分组"
        return label, sources, mode

    return "当前范围：全部资料", [], SCOPE_ALL


def unindexed_sources(sources: list[str], indexed_paths: set[str]) -> list[str]:
    return [source for source in sources if source not in indexed_paths]


def current_range_indexed_gap(
    materials: list[dict],
    scope_label: str,
    selected_sources: list[str],
    indexed_paths: set[str],
) -> tuple[list[str], list[str], bool]:
    if selected_sources:
        range_sources = selected_sources
        return range_sources, unindexed_sources(range_sources, indexed_paths), False

    if "全部资料" not in scope_label:
        return [], [], False

    range_sources = [material["relative_path"] for material in materials]
    return range_sources, unindexed_sources(range_sources, indexed_paths), True
