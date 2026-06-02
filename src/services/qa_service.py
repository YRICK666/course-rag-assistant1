from __future__ import annotations

from ask import answer_question
from llm_deepseek import DeepSeekError, is_deepseek_configured, rewrite_query
from retriever import collection_count, display_hits, format_source
from services.scope_service import unindexed_sources


DEEPSEEK_TOP_K = 16


def answer_source_references(hits: list[dict]) -> list[dict]:
    references: list[dict] = []
    visible_hits = [hit for hit in hits if not hit.get("is_neighbor")][:DEEPSEEK_TOP_K]
    if not visible_hits:
        visible_hits = display_hits(hits)
    for hit in visible_hits:
        metadata = hit.get("metadata", {})
        similarity = hit.get("similarity")
        hybrid_score = hit.get("hybrid_score")
        keyword_score = hit.get("keyword_score")
        target_score = hit.get("target_score")
        score_text = f" · 相似度 {similarity:.3f}" if similarity is not None else ""
        if hybrid_score is not None:
            score_text += f" · 混合分 {hybrid_score:.3f}"
        if keyword_score:
            score_text += f" · 关键词分 {keyword_score:.3f}"
        if target_score:
            score_text += f" · 目标词分 {target_score:.3f}"
        neighbor_text = " · 相邻段落" if hit.get("is_neighbor") else ""
        references.append(
            {
                "rank": hit.get("rank"),
                "title": f"[{hit['rank']}] {format_source(metadata)}{score_text}{neighbor_text}",
                "source_path": metadata.get("source_path", ""),
                "text": hit.get("text", ""),
                "expanded": hit.get("rank", 0) <= 2,
                "metadata": metadata,
                "hit": hit,
            }
        )
    return references


def answer_error(message: str, *, missing_sources: list[str] | None = None) -> dict:
    return {
        "success": False,
        "answer": None,
        "warning": None,
        "hits": [],
        "sources": [],
        "error": message,
        "missing_sources": missing_sources or [],
    }


def answer_success(
    answer: str,
    hits: list[dict],
    warning: str | None,
    *,
    answer_mode: str,
    rewritten_query: str | None = None,
) -> dict:
    return {
        "success": True,
        "answer": answer,
        "warning": warning,
        "hits": hits,
        "sources": answer_source_references(hits),
        "error": None,
        "missing_sources": [],
        "answer_mode": answer_mode,
        "rewritten_query": rewritten_query,
    }


def qa_answer_mode(use_deepseek: bool) -> str:
    if use_deepseek and is_deepseek_configured():
        return "DeepSeek 增强回答"
    return "本地回退回答"


def rewrite_question_for_retrieval(question: str, selected_sources: list[str], use_deepseek: bool) -> str | None:
    if not (use_deepseek and is_deepseek_configured()):
        return None
    try:
        rewritten = rewrite_query(question, selected_sources or None)
    except DeepSeekError:
        return None
    rewritten = str(rewritten).strip()
    if not rewritten or rewritten == question:
        return None
    return rewritten


def validate_question_request(
    subject_paths,
    *,
    question: str,
    selected_sources: list[str],
    indexed_paths: set[str],
) -> dict | None:
    question = str(question).strip()
    if not question:
        return answer_error("请先输入问题。")

    if collection_count(outputs_dir=subject_paths.outputs_dir) == 0:
        return answer_error("当前科目知识库为空。请先上传资料并重建知识库。")

    missing_sources = unindexed_sources(selected_sources, indexed_paths) if selected_sources else []
    if missing_sources:
        return answer_error(
            "所选资料尚未进入知识库，请先重建所选资料知识库或添加/更新所选资料。",
            missing_sources=missing_sources,
        )

    return None


def ask_course_question(
    subject_paths,
    *,
    question: str,
    top_k: int,
    use_deepseek: bool,
    selected_sources: list[str],
    indexed_paths: set[str],
) -> dict:
    validation_error = validate_question_request(
        subject_paths,
        question=question,
        selected_sources=selected_sources,
        indexed_paths=indexed_paths,
    )
    if validation_error:
        return validation_error

    question = str(question).strip()
    answer_mode = qa_answer_mode(use_deepseek)
    rewritten_query = rewrite_question_for_retrieval(question, selected_sources, use_deepseek)
    retrieval_top_k = max(int(top_k), DEEPSEEK_TOP_K) if answer_mode == "DeepSeek 增强回答" else int(top_k)
    try:
        answer, hits, warning = answer_question(
            question=question,
            top_k=retrieval_top_k,
            use_deepseek=use_deepseek,
            retrieval_question=rewritten_query,
            source_filters=selected_sources or None,
            outputs_dir=subject_paths.outputs_dir,
        )
    except Exception as exc:
        return answer_error(f"查询失败：{exc}")

    if warning and "DeepSeek 调用失败" in warning:
        answer_mode = "本地回退回答"

    return answer_success(
        answer,
        hits,
        warning,
        answer_mode=answer_mode,
        rewritten_query=rewritten_query,
    )
