from __future__ import annotations

import logging
from pathlib import Path

from ingest import build_index
from material_manager import batch_convert_ppt_materials

logger = logging.getLogger(__name__)


def clear_subject_index_caches(subject_paths) -> dict[str, bool]:
    cache_paths = {
        "chapter_summaries": subject_paths.chapter_summaries_path,
        "study_guides": Path(subject_paths.outputs_dir) / "study_guides.json",
    }
    cleared: dict[str, bool] = {}
    for name, cache_path in cache_paths.items():
        cache_path = Path(cache_path)
        if cache_path.exists():
            cache_path.unlink()
            cleared[name] = True
        else:
            cleared[name] = False
    return cleared


def any_cache_cleared(caches_cleared: dict[str, bool]) -> bool:
    return any(caches_cleared.values())


def cache_message(caches_cleared: dict[str, bool], *, include_missing_message: bool) -> str:
    if any_cache_cleared(caches_cleared):
        return "资料概览缓存已清理。"
    if include_missing_message:
        return "未发现旧的资料概览缓存。"
    return ""


def conversion_failure_details(conversion: dict, limit: int = 3) -> str:
    failures = conversion.get("failures") or []
    return "；".join(
        f"{item.get('file_name')}: {item.get('message')}"
        for item in failures[:limit]
    )


def notice_kind_for_summary(summary: dict) -> str:
    conversion = summary.get("ppt_conversion") or {}
    if conversion.get("failure_count") or summary.get("chunk_count", 0) == 0:
        return "warning"
    return "success"


def full_rebuild_message(summary: dict, caches_cleared: dict[str, bool]) -> str:
    conversion = summary.get("ppt_conversion") or {}
    cache_text = cache_message(caches_cleared, include_missing_message=True)
    conversion_text = (
        f" PPT 转换：成功 {conversion.get('success_count', 0)} 个，"
        f"失败 {conversion.get('failure_count', 0)} 个。"
    )
    if conversion.get("success_count", 0):
        conversion_text += " 已转换的原始 PPT 已归档。"
    if conversion.get("failure_count", 0):
        details = conversion_failure_details(conversion)
        conversion_text += f" 转换失败的 PPT 已跳过，未参与建库。失败原因：{details}"
    if summary.get("chunk_count", 0) == 0:
        conversion_text += " 知识库仍为 0 块，可能是 PPT 转换失败，或资料无法提取文本。"

    return (
        f"建库完成。本次建库范围：全部资料；处理文件 {summary.get('file_count', 0)} 个；"
        f"chunk_count={summary.get('chunk_count', 0)}；chroma_count={summary.get('chroma_count', 0)}。"
        f"当前知识库包含全部资料。{cache_text}{conversion_text}"
    )


def selected_build_message(summary: dict, scope_label: str, caches_cleared: dict[str, bool], *, reset: bool) -> str:
    conversion = summary.get("ppt_conversion") or {}
    knowledge_text = "当前知识库仅包含所选资料。" if reset else "当前知识库已添加/更新所选资料。"
    message = (
        f"建库完成。本次建库范围：{scope_label}；"
        f"处理文件 {summary.get('file_count', 0)} 个；"
        f"PPT 转换成功 {conversion.get('success_count', 0)} 个，"
        f"失败 {conversion.get('failure_count', 0)} 个；"
        f"chunk_count={summary.get('chunk_count', 0)}；"
        f"chroma_count={summary.get('chroma_count', 0)}。"
        f"{knowledge_text}"
    )
    if conversion.get("failures"):
        message += f" 转换失败原因：{conversion_failure_details(conversion)}"
    if summary.get("chunk_count", 0) == 0:
        message += " 知识库仍为 0 块，可能是转换失败或资料无法提取文本。"
    cache_text = cache_message(caches_cleared, include_missing_message=False)
    if cache_text:
        message += f" {cache_text}"
    return message


def build_result(
    summary: dict,
    *,
    scope_label: str,
    caches_cleared: dict[str, bool],
    message: str,
    notice_kind: str,
) -> dict:
    chunk_count = summary.get("chunk_count", 0)
    chroma_count = summary.get("chroma_count", 0)
    success = chroma_count > 0 or chunk_count == 0
    if chunk_count > 0 and chroma_count == 0:
        logger.warning("chunk_count=%d > 0 but chroma_count=0 — Chroma 写入为空", chunk_count)

    messages = [message]
    if not success:
        messages.append("Chroma 写入为空，知识库可能未正确构建。")

    return {
        **summary,
        "success": success,
        "scope_label": scope_label,
        "file_count": summary.get("file_count", 0),
        "chunk_count": chunk_count,
        "chroma_count": chroma_count,
        "ppt_conversion": summary.get("ppt_conversion") or {},
        "indexed_files": summary.get("indexed_files") or [],
        "caches_cleared": caches_cleared,
        "cache_cleared": any_cache_cleared(caches_cleared),
        "notice_kind": notice_kind,
        "message": message,
        "messages": messages,
    }


def rebuild_all_materials_index(
    subject_paths,
    *,
    chunk_size: int,
    overlap: int,
    batch_size: int,
    embedding_model: str,
) -> dict:
    conversion_result = batch_convert_ppt_materials(subject_paths)
    summary = build_index(
        chunk_size=chunk_size,
        overlap=overlap,
        batch_size=batch_size,
        embedding_model=embedding_model,
        reset=True,
        materials_dir=subject_paths.materials_dir,
        outputs_dir=subject_paths.outputs_dir,
    )
    summary["ppt_conversion"] = conversion_result
    caches_cleared = clear_subject_index_caches(subject_paths)
    return build_result(
        summary,
        scope_label="全部资料",
        caches_cleared=caches_cleared,
        message=full_rebuild_message(summary, caches_cleared),
        notice_kind=notice_kind_for_summary(summary),
    )


def build_selected_materials_index(
    subject_paths,
    selected_files: list[str],
    *,
    reset: bool,
    scope_label: str,
    chunk_size: int,
    overlap: int,
    batch_size: int,
    embedding_model: str,
) -> dict:
    if not selected_files:
        raise ValueError("请先选择要建库的资料。")

    summary = build_index(
        chunk_size=chunk_size,
        overlap=overlap,
        batch_size=batch_size,
        embedding_model=embedding_model,
        reset=reset,
        materials_dir=subject_paths.materials_dir,
        outputs_dir=subject_paths.outputs_dir,
        selected_files=selected_files,
    )
    caches_cleared = clear_subject_index_caches(subject_paths)
    return build_result(
        summary,
        scope_label=scope_label,
        caches_cleared=caches_cleared,
        message=selected_build_message(summary, scope_label, caches_cleared, reset=reset),
        notice_kind=notice_kind_for_summary(summary),
    )
