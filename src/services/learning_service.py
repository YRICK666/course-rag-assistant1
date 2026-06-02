from __future__ import annotations

from llm_deepseek import get_deepseek_model, is_deepseek_configured
from overview import (
    fetch_all_records,
    get_chapter_overview,
    get_collection,
    get_study_guide,
    infer_title,
    load_cache,
    make_study_guide_cache_key,
    records_signature,
    resolve_overview_outputs_dir,
    resolve_study_guide_cache_path,
)
from retriever import detect_chapter_number


def overview_result_dict(result) -> dict:
    return {
        "success": True,
        "title": result.title,
        "content": result.content,
        "sources": result.sources,
        "references": result.sources[:5],
        "cached": result.cached,
        "warning": result.warning,
        "error": None,
        "cache_key": result.cache_key,
    }


def study_guide_result_dict(result) -> dict:
    return {
        "success": True,
        "title": result.title,
        "content": result.content,
        "sources": result.sources,
        "references": result.sources[:5],
        "cached": result.cached,
        "warning": result.warning,
        "error": None,
        "cache_key": result.cache_key,
    }


def generate_all_materials_overview(
    subject_paths,
    *,
    use_deepseek: bool,
    force_refresh: bool = False,
) -> dict:
    result = get_chapter_overview(
        question="概括全部课程资料的主要内容",
        use_deepseek=use_deepseek,
        force_refresh=force_refresh,
        outputs_dir=subject_paths.outputs_dir,
    )
    return overview_result_dict(result)


def generate_current_material_overview(
    subject_paths,
    selected_sources: list[str],
    *,
    use_deepseek: bool,
    force_refresh: bool = False,
) -> dict:
    selected_chapter = detect_chapter_number(selected_sources[0]) if len(selected_sources) == 1 else None
    result = get_chapter_overview(
        question="概括当前资料主要内容",
        chapter=selected_chapter,
        source_filters=selected_sources,
        use_deepseek=use_deepseek,
        force_refresh=force_refresh,
        outputs_dir=subject_paths.outputs_dir,
    )
    return overview_result_dict(result)


def generate_study_guide(
    subject_paths,
    selected_sources: list[str],
    *,
    use_deepseek: bool,
    force_refresh: bool = False,
) -> dict:
    result = get_study_guide(
        subject_name=subject_paths.name,
        source_filters=selected_sources or None,
        use_deepseek=use_deepseek,
        force_refresh=force_refresh,
        outputs_dir=subject_paths.outputs_dir,
    )
    return study_guide_result_dict(result)


def get_study_guide_cache_status(
    subject_paths,
    selected_sources: list[str],
    *,
    use_deepseek: bool,
) -> dict:
    source_filters = [item for item in (selected_sources or []) if item]
    resolved_outputs_dir = resolve_overview_outputs_dir(subject_paths.outputs_dir)
    resolved_cache_path = resolve_study_guide_cache_path(outputs_dir=resolved_outputs_dir)

    try:
        collection = get_collection(create=False, outputs_dir=resolved_outputs_dir)
        records = fetch_all_records(
            collection,
            source_paths=set(source_filters) if source_filters else None,
        )
    except Exception:
        return {"exists": False, "cache_key": None, "signature": None}

    if not records:
        return {"exists": False, "cache_key": None, "signature": None}

    title = infer_title(records, source_filters=source_filters or None)
    signature = records_signature(records)
    generator = f"deepseek:{get_deepseek_model()}" if use_deepseek and is_deepseek_configured() else "local"
    cache_key = make_study_guide_cache_key(
        subject_name=subject_paths.name,
        title=title,
        source_filters=source_filters,
        generator=generator,
        signature=signature,
    )

    cache = load_cache(resolved_cache_path)
    cached_item = cache.get(cache_key, {})
    exists = bool(cached_item.get("content"))
    if not exists:
        cached_item = next(
            (
                item
                for item in cache.values()
                if isinstance(item, dict)
                and item.get("subject_name") == subject_paths.name
                and list(item.get("source_filters") or []) == source_filters
                and item.get("signature") == signature
                and bool(item.get("content"))
            ),
            {},
        )
        exists = bool(cached_item.get("content"))

    return {
        "exists": exists,
        "cache_key": cache_key,
        "signature": signature,
        "title": str(cached_item.get("title") or title) if exists else None,
        "content": str(cached_item.get("content") or "") if exists else None,
        "sources": list(cached_item.get("sources") or []) if exists else [],
    }
