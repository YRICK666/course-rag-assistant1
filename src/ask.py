from __future__ import annotations

import argparse

from ai_settings import load_ai_settings
from llm_deepseek import DeepSeekError, _is_long_form_task, generate_answer, is_deepseek_configured
from overview import get_chapter_overview
from retriever import (
    DEFAULT_TOP_K,
    clean_math_text,
    collection_count,
    display_hits,
    format_source,
    make_local_summary,
    retrieve,
)


AI_DISABLED_WARNING = "AI 已关闭，仍可查看资料来源和页面预览。"


def print_sources(hits: list[dict]) -> None:
    visible_hits = display_hits(hits)
    if not visible_hits:
        return

    print("\n来源：")
    for hit in visible_hits:
        metadata = hit.get("metadata", {})
        similarity = hit.get("similarity")
        hybrid_score = hit.get("hybrid_score")
        keyword_score = hit.get("keyword_score")
        target_score = hit.get("target_score")
        score_text = f"，相似度 {similarity:.3f}" if similarity is not None else ""
        if hybrid_score is not None:
            score_text += f"，混合分 {hybrid_score:.3f}"
        if keyword_score:
            score_text += f"，关键词分 {keyword_score:.3f}"
        if target_score:
            score_text += f"，目标词分 {target_score:.3f}"
        neighbor_text = "，相邻段落" if hit.get("is_neighbor") else ""
        print(f"[{hit['rank']}] {format_source(metadata)}{score_text}{neighbor_text}")
        print(f"    {metadata.get('source_path', '')}")


def print_snippets(hits: list[dict], max_chars: int = 500) -> None:
    visible_hits = display_hits(hits)
    if not visible_hits:
        return

    print("\n相关片段：")
    for hit in visible_hits:
        text = clean_math_text(" ".join(hit.get("text", "").split()))
        if len(text) > max_chars:
            text = text[:max_chars] + "..."
        print(f"\n[{hit['rank']}] {text}")


def print_overview_sources(sources: list[dict[str, str]]) -> None:
    if not sources:
        return

    print("\n来源：")
    for source in sources:
        print(f"[{source.get('rank', '')}] {source.get('label', '')}")
        source_path = source.get("source_path")
        if source_path:
            print(f"    {source_path}")


def answer_question(
    question: str,
    top_k: int = DEFAULT_TOP_K,
    use_deepseek: bool = True,
    chapter: int | None = None,
    source: str | None = None,
    source_filters: list[str] | None = None,
    retrieval_question: str | None = None,
    outputs_dir=None,
    chroma_dir=None,
) -> tuple[str, list[dict], str | None]:
    is_long_form = _is_long_form_task(question)
    retrieval_query = (retrieval_question or question).strip()
    hits = retrieve(
        retrieval_query,
        top_k=top_k,
        broad_mode=is_long_form,
        chapter=chapter,
        source=source,
        source_filters=source_filters,
        outputs_dir=outputs_dir,
        chroma_dir=chroma_dir,
    )
    warning: str | None = None
    ai_enabled = load_ai_settings()["enabled"]

    if use_deepseek and not ai_enabled:
        warning = AI_DISABLED_WARNING
    elif use_deepseek and is_deepseek_configured():
        try:
            return clean_math_text(generate_answer(question, hits, long_form=is_long_form)), hits, None
        except DeepSeekError as exc:
            warning = f"DeepSeek 调用失败，已自动切换本地回退。{exc}"

    return clean_math_text(make_local_summary(question, hits)), hits, warning


def main() -> None:
    parser = argparse.ArgumentParser(description="Ask questions against the local course-material RAG index.")
    parser.add_argument("question", nargs="*", help="Question to ask. If omitted, interactive input is used.")
    parser.add_argument("--top-k", type=int, default=DEFAULT_TOP_K, help="Number of chunks to retrieve.")
    parser.add_argument("--chapter", type=int, choices=range(1, 10), help="Only search materials for this chapter.")
    parser.add_argument("--source", help="Only search files whose name or path contains this text.")
    parser.add_argument("--overview", action="store_true", help="Generate a chapter overview from indexed chunks.")
    parser.add_argument("--no-deepseek", action="store_true", help="Disable DeepSeek even if .env is configured.")
    args = parser.parse_args()

    question = " ".join(args.question).strip()
    if not question:
        question = input("请输入问题：").strip()

    if not question:
        print("问题为空。")
        return

    if args.overview:
        result = get_chapter_overview(
            question=question,
            chapter=args.chapter,
            source=args.source,
            use_deepseek=not args.no_deepseek,
        )
        if result.warning:
            print(result.warning)
            print()
        if result.cached:
            print("已读取缓存的章节概览。")
            print()
        print("答案：")
        print(result.content)
        print_overview_sources(result.sources)
        return

    if collection_count() == 0:
        print("知识库为空。请先运行：python src/ingest.py")
        return

    answer, hits, warning = answer_question(
        question=question,
        top_k=args.top_k,
        use_deepseek=not args.no_deepseek,
        chapter=args.chapter,
        source=args.source,
    )

    if warning:
        print(warning)
        print()

    print("答案：")
    print(answer)
    print_sources(hits)

    if args.no_deepseek or not is_deepseek_configured():
        print_snippets(hits)


if __name__ == "__main__":
    main()
