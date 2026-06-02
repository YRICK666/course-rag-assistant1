from __future__ import annotations

import os
import json
import re
from collections import defaultdict
from typing import Any

import requests
from dotenv import load_dotenv

from ai_settings import DEFAULT_BASE_URL, DEFAULT_MODEL, ProviderName, load_ai_settings
from retriever import PROJECT_ROOT, clean_math_text, display_hits, format_source


load_dotenv(PROJECT_ROOT / ".env")

SNIPPET_KEYWORD_STOP_WORDS = {
    "课程",
    "学习",
    "掌握",
    "了解",
    "基本",
    "主要",
    "能力",
    "内容",
    "重点",
    "部分",
    "知识",
    "概念",
}


class DeepSeekError(RuntimeError):
    pass


def get_ai_provider() -> ProviderName:
    return load_ai_settings()["provider"]


def get_llm_provider_label() -> str:
    return "OpenAI-compatible" if get_ai_provider() == "openai_compatible" else "DeepSeek"


def get_deepseek_api_key() -> str:
    settings = load_ai_settings()
    return settings["api_key"] if settings["enabled"] else ""


def get_deepseek_base_url() -> str:
    return load_ai_settings()["base_url"].strip() or DEFAULT_BASE_URL


def get_deepseek_model() -> str:
    return load_ai_settings()["model"].strip() or DEFAULT_MODEL


def is_deepseek_configured() -> bool:
    return bool(get_deepseek_api_key())


def get_chat_completions_url() -> str:
    settings = load_ai_settings()
    if settings["provider"] == "openai_compatible":
        base_url = settings["base_url"].strip().rstrip("/")
        if not base_url:
            raise DeepSeekError("OpenAI-compatible base_url is not configured.")
        return f"{base_url}/chat/completions"
    base_url = (settings["base_url"].strip() or DEFAULT_BASE_URL).rstrip("/")
    return f"{base_url}/chat/completions"


def post_chat_completions(payload: dict[str, Any], *, timeout: int) -> dict[str, Any]:
    ensure_ai_enabled()
    api_key = get_deepseek_api_key()
    provider_label = get_llm_provider_label()
    if not api_key:
        raise DeepSeekError(f"{provider_label} API key is not configured.")

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    try:
        response = requests.post(get_chat_completions_url(), headers=headers, json=payload, timeout=timeout)
        response.raise_for_status()
        return response.json()
    except requests.RequestException as exc:
        raise DeepSeekError(f"{provider_label} request failed: {exc}") from exc


def ensure_ai_enabled() -> None:
    if not load_ai_settings()["enabled"]:
        raise DeepSeekError("AI is disabled.")


def _normalize_keyword_list(payload: Any, source_text: str, limit: int = 5) -> list[str]:
    if isinstance(payload, dict):
        raw_keywords = payload.get("keywords", [])
    elif isinstance(payload, list):
        raw_keywords = payload
    else:
        raw_keywords = []

    keywords: list[str] = []
    source_lower = source_text.lower()
    for item in raw_keywords:
        keyword = clean_math_text(str(item)).strip(" \t\r\n\"'“”‘’，。；;：:")
        if (
            not keyword
            or len(keyword) < 2
            or len(keyword) > 30
            or keyword in SNIPPET_KEYWORD_STOP_WORDS
            or keyword.isdigit()
        ):
            continue
        if keyword.lower() not in source_lower:
            continue
        if any(existing.lower() == keyword.lower() for existing in keywords):
            continue
        keywords.append(keyword)
        if len(keywords) >= limit:
            break
    return keywords


def extract_snippet_keywords(text: str) -> list[str]:
    source_text = clean_math_text(" ".join((text or "").split()))
    if not source_text:
        return []

    clipped_text = source_text[:1500]
    payload = {
        "model": get_deepseek_model(),
        "messages": [
            {
                "role": "system",
                "content": (
                    "你是课程复习关键词提取器。"
                    "只从用户给出的原文片段中选择最值得高亮的复习关键词，不能改写，不能编造。"
                    "优先选择概念、术语、定理、方法名、模型名、公式名。"
                    "不要选择课程、学习、掌握、了解、基本、主要、能力等泛词。"
                    "只输出 JSON，格式为 {\"keywords\":[\"关键词1\",\"关键词2\"]} 或 [\"关键词1\",\"关键词2\"]。"
                ),
            },
            {
                "role": "user",
                "content": (
                    "请从下面片段中提取 3 到 5 个最适合学生复习时高亮的关键词。"
                    "关键词必须是原文中连续出现的原词，优先课程概念、定义名、模型名、公式名、方法名。"
                    "不要编造原文没有的词，不要选泛泛的学习目标词。"
                    "不要输出解释，不要输出 Markdown。\n\n"
                    f"片段：{clipped_text}"
                ),
            },
        ],
        "temperature": 0.0,
        "stream": False,
    }

    try:
        data = post_chat_completions(payload, timeout=20)
        content = str(data["choices"][0]["message"]["content"]).strip()
        match = re.search(r"\{.*\}|\[.*\]", content, flags=re.S)
        parsed = json.loads(match.group(0) if match else content)
        return _normalize_keyword_list(parsed, source_text)
    except (KeyError, IndexError, ValueError, json.JSONDecodeError) as exc:
        raise DeepSeekError(f"Unexpected {get_llm_provider_label()} keyword response: {exc}") from exc


def select_context_hits(hits: list[dict[str, Any]], max_hits: int = 12, source_aware: bool = False) -> list[dict[str, Any]]:
    primary_hits = [hit for hit in hits if not hit.get("is_neighbor")]
    if not primary_hits:
        return []

    if source_aware:
        by_source: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for hit in primary_hits:
            sp = str(hit.get("metadata", {}).get("source_path") or hit.get("metadata", {}).get("file_name") or "unknown")
            by_source[sp].append(hit)

        selected: list[dict[str, Any]] = []
        selected_ids: set[str] = set()
        source_lists = list(by_source.values())
        while len(selected) < max_hits and any(source_lists):
            for lst in source_lists:
                if not lst:
                    continue
                hit = lst.pop(0)
                hid = str(hit.get("id") or "")
                if hid in selected_ids:
                    continue
                selected.append(hit)
                selected_ids.add(hid)
                if len(selected) >= max_hits:
                    break
        return selected

    selected = []
    selected_ids = set()
    seen_sources = set()

    for hit in primary_hits:
        metadata = hit.get("metadata", {})
        source_path = str(metadata.get("source_path") or metadata.get("file_name") or "")
        hit_id = str(hit.get("id") or f"{source_path}|{hit.get('rank')}")
        if source_path in seen_sources or hit_id in selected_ids:
            continue
        selected.append(hit)
        selected_ids.add(hit_id)
        seen_sources.add(source_path)
        if len(selected) >= max_hits:
            return selected

    for hit in primary_hits:
        metadata = hit.get("metadata", {})
        source_path = str(metadata.get("source_path") or metadata.get("file_name") or "")
        hit_id = str(hit.get("id") or f"{source_path}|{hit.get('rank')}")
        if hit_id in selected_ids:
            continue
        selected.append(hit)
        selected_ids.add(hit_id)
        if len(selected) >= max_hits:
            break

    return selected


def build_context(hits: list[dict[str, Any]], max_chars_per_hit: int = 1200, max_hits: int = 12, source_aware: bool = False) -> str:
    context_parts: list[str] = []
    visible_hits = select_context_hits(hits, max_hits=max_hits, source_aware=source_aware)

    for hit in visible_hits:
        metadata = hit.get("metadata", {})
        source = format_source(metadata)
        source_path = metadata.get("source_path", "")
        text = clean_math_text(" ".join(hit.get("text", "").split()))
        if len(text) > max_chars_per_hit:
            text = text[:max_chars_per_hit] + "..."
        context_parts.append(f"[{hit['rank']}] 来源：{source}\n文件：{source_path}\n原文片段：{text}")

        neighbor_texts: list[str] = []
        for neighbor in hits:
            if not neighbor.get("is_neighbor"):
                continue
            if neighbor.get("parent_rank") != hit.get("rank"):
                continue
            text = clean_math_text(" ".join(neighbor.get("text", "").split()))
            if len(text) > 600:
                text = text[:600] + "..."
            neighbor_texts.append(text)

        if neighbor_texts:
            context_parts.append(f"[{hit['rank']}] 相邻页/段落补充上下文：\n" + "\n".join(neighbor_texts[:3]))

    if not context_parts:
        for hit in hits[:5]:
            source = format_source(hit.get("metadata", {}))
            text = clean_math_text(" ".join(hit.get("text", "").split()))
            if len(text) > max_chars_per_hit:
                text = text[:max_chars_per_hit] + "..."
            context_parts.append(f"[{hit['rank']}] 来源：{source}\n{text}")
    return "\n\n".join(context_parts)


def rewrite_query(question: str, source_filters: list[str] | None = None) -> str:
    scope = "全部资料"
    if source_filters:
        scope = "；".join(source_filters[:8])
        if len(source_filters) > 8:
            scope += f" 等 {len(source_filters)} 个资料"

    payload = {
        "model": get_deepseek_model(),
        "messages": [
            {
                "role": "system",
                "content": (
                    "你负责把学生问题改写成更适合课程资料向量检索的中文查询。"
                    "不能改变用户原意，不能回答问题，只输出一条改写后的检索查询。"
                    "如果原问题已经清楚，就保留核心问题并补充同义关键词。"
                ),
            },
            {
                "role": "user",
                "content": (
                    f"当前资料范围：{scope}\n"
                    f"学生问题：{question}\n\n"
                    "请输出更适合检索课程 PPT/PDF/文档片段的查询。"
                    "对“这一章讲了什么、重点是什么、这个主要讲啥”这类问题，"
                    "改写为包含“主题、主要内容、核心概念、重点难点、复习建议”的查询。"
                    "不要添加资料范围外的新事实。"
                ),
            },
        ],
        "temperature": 0.0,
        "stream": False,
    }

    try:
        data = post_chat_completions(payload, timeout=30)
        rewritten = str(data["choices"][0]["message"]["content"]).strip().strip('"“”')
    except (KeyError, IndexError, ValueError) as exc:
        raise DeepSeekError(f"Unexpected {get_llm_provider_label()} rewrite response: {exc}") from exc

    return clean_math_text(rewritten) or question


def _is_writing_task(question: str) -> bool:
    """Detect if the user request is a writing / analysis /整理 task."""
    patterns = [
        r"写.*读后感",
        r"写.*观后感",
        r"写.*论文",
        r"写.*讲稿",
        r"写.*总结",
        r"写.*作业",
        r"写.*文章",
        r"扩写",
        r"撰写",
        r"不少于.*\d+.*字",
        r"写成.*文章",
        r"写一份",
        r"写一段",
        r"写一篇",
        r"写个",
        r"资料分析",
        r"章节梳理",
        r"复习笔记",
        r"课程报告",
        r"心得体会",
        r"论文式.*综述",
        r"输出.*\d+.*字",
        r"分析.*资料",
        r"整理.*笔记",
        r"梳理.*[章节知识点]",
        r"综述",
    ]
    return any(re.search(p, question) for p in patterns)


def _is_long_form_task(question: str) -> bool:
    """Detect if the user request requires broad coverage across multiple sources/chapters."""
    patterns = [
        r"全书",
        r"全部资料",
        r"所有[章节资料]",
        r"系统[整理梳理]",
        r"综合[分析阐述论述]",
        r"整[本门][书课]",
        r"通篇",
        r"各个[章节方面]",
        r"多[维角].*[度面].*[分析阐述]",
        r"全面[分析综述总结回顾]",
        r"第1[章节].*最后",
    ]
    return any(re.search(p, question) for p in patterns)


def _writing_mode_system_prompt(question: str) -> str:
    prompt = (
        "你是这门课的课程助教，正在辅助学生完成资料整理或写作任务。"
        "用户的请求属于\"资料依据整理模式\"（如资料分析、章节梳理、复习笔记、"
        "课程报告、读后感、心得体会、论文式综述、讲稿、扩写文章等），"
        "不要把用户的写作要求误判为\"资料中是否存在该要求\"。"
        "用户要求的字数、详尽程度、文体格式属于输出形式要求，不需要资料中明确出现。"
        "事实性内容必须基于提供的课程资料片段；如果资料不足，应说明"
        "\"以下基于当前已检索资料整理\"，但不要直接拒绝，仍应围绕已有资料展开。"
        "允许使用通用写作结构、分析框架、过渡句和总结性表达。"
        "禁止编造资料中没有的具体事实、页码、人物经历、原文引文。"
        "来源编号 [1][2] 只挂在基于资料的关键事实后面，不要每句话都挂，不要破坏文章流畅性。"
        "如果用户明确要求\"不要引用角标\"或\"不要加引用\"，可以减少或省略角标，但仍必须基于资料。"
        "不要使用 Markdown 表格。不要使用 # 或 ## 标题。"
        "数学符号使用 Unicode 普通字符，不用 LaTeX 包裹。"
        "对于长文任务，建议采用以下结构：\n"
        "标题\n"
        "引言 / 总述\n"
        "材料背景\n"
        "核心内容分析\n"
        "重点概念或主题\n"
        "学习/研究启发\n"
        "结论"
        "如果用户要求 3000 字以上，应尽量完整展开；"
        "如果可能超过单次输出限制，应在结尾提示\"以上为第一部分，可继续生成下一部分\"。"
    )
    if re.search(r"读后感|观后感|心得体会", question):
        prompt += (
            "\n\n对于读后感 / 心得体会类任务，建议结构：\n"
            "标题\n"
            "开头：阅读对象与总体感受\n"
            "故事/文本概述：时间、人物、事件\n"
            "感悟一：文本核心问题\n"
            "感悟二：与课程主题的关系\n"
            "感悟三：现实或方法论启发\n"
            "结尾：总结"
        )
    return prompt


def _detect_requested_word_count(question: str) -> int | None:
    """Extract user-requested character/word count from the question."""
    match = re.search(r"(\d{3,5})\s*[字]", question)
    if match:
        count = int(match.group(1))
        if 200 <= count <= 20000:
            return count
    return None


def _get_completion_token_limit(question: str, is_writing_task: bool) -> int:
    """Determine the max_tokens value based on task type and requested word count."""
    if not is_writing_task:
        return 4000
    word_count = _detect_requested_word_count(question)
    if word_count is None:
        return 6000
    if word_count >= 5000:
        return 12000
    if word_count >= 3000:
        return 8000
    return 6000


def _get_timeout(is_writing_task: bool, word_count: int | None) -> int:
    """Return appropriate request timeout based on task type and expected output length."""
    if not is_writing_task:
        return 90
    if word_count and word_count >= 5000:
        return 180
    if word_count and word_count >= 3000:
        return 150
    return 120


def generate_answer(question: str, hits: list[dict[str, Any]], long_form: bool = False) -> str:
    if not hits:
        return "资料库中没有检索到足够相关的内容，因此无法基于课程资料回答。"

    model = get_deepseek_model()
    is_writing = _is_writing_task(question)
    is_long = long_form or _is_long_form_task(question)
    word_count = _detect_requested_word_count(question)

    # Broad retrieval context for long-form or large writing tasks
    if is_long or (word_count and word_count >= 3000):
        context = build_context(hits, max_hits=40, source_aware=True)
    else:
        context = build_context(hits)

    max_tokens = _get_completion_token_limit(question, is_writing or is_long)
    timeout = _get_timeout(is_writing or is_long, word_count)

    if is_writing or is_long:
        length_req = ""
        if word_count and word_count >= 3000:
            length_req = (
                f"\n\n用户要求输出约 {word_count} 字的长文，请遵守以下长度要求：\n"
                "1. 不要压缩为摘要，必须展开每个小节，每节至少若干段。\n"
                "2. 优先输出完整正文，而不是只给提纲。\n"
                "3. 不要承诺精确字数，不要编造资料外事实。"
            )
            if word_count >= 5000:
                length_req += (
                    f"\n4. 目标输出不少于 {word_count - 1000} 中文字。\n"
                    "5. 如果无法一次完成，先输出完整的第一部分，并在结尾提示"
                    "\"以上为第一部分，可继续生成下一部分\"。"
                )

        coverage_req = ""
        if is_long:
            coverage_req = (
                "\n\n本任务需要覆盖多个资料/章节，请遵守以下覆盖要求：\n"
                "1. 以下提供了来自多个章节/资料的片段，请尽量从不同来源提取信息进行多维度整理。\n"
                "2. 不要仅依赖前几个片段的内容，应综合各来源的信息进行全面阐述。\n"
                "3. 如果某些章节或资料的内容有侧重差异，应分别说明并加以整合。\n"
                "4. 按主题或逻辑层次组织内容（如概念基础→核心原理→应用分析），而不是按资料来源罗列。\n"
                "5. 覆盖不足时说明\"以下基于当前已检索的部分章节整理\"，以便用户补充。"
            )

        system_prompt = _writing_mode_system_prompt(question)
        temperature = 0.4
        user_prompt = (
            f"任务：{question}\n\n"
            f"课程资料片段：\n{context}\n\n"
            "请基于以上资料片段完成任务。要求：\n"
            "1. 不要以\"资料没有该要求\"为由拒绝，用户要求的文体、字数属于输出形式要求。\n"
            "2. 事实基于资料，不足时说明\"以下基于当前已检索资料整理\"，但仍继续展开。\n"
            "3. 允许使用通用写作结构、分析框架和过渡句。\n"
            "4. 来源编号 [1][2] 只标在基于资料的关键事实后面，保持文章流畅；"
            "如果用户明确要求不要引用角标，可以减少或省略。\n"
            "5. 禁止编造资料中没有的具体事实、页码、原文引文。\n"
            "6. 字数不足时先说明再继续写初稿；长文任务尽量展开，超限时提示可继续生成下一部分。"
            f"{length_req}"
            f"{coverage_req}"
        )
    else:
        system_prompt = (
            "你是这门课的课程助教，正在考前答疑。"
            "回答要直接、自然，像助教在面授，帮助学生理解和复习，不要写成报告或百科总结。"
            "只能根据提供的课程资料片段回答，不能补充课件没有的外部细节。"
            "第一句必须直接回答问题，禁止使用"
            "“根据资料”“好的，下面为您解答”“综上所述”“总而言之”“从资料可以看出”"
            "“值得注意的是”“本文档主要”“以上内容为你梳理”等套话。"
            "默认回答 300 到 600 字；简单概念题可以略短；复杂比较题、步骤题可以接近 600 字，但不要冗长。"
            "优先用短段落讲清楚；比较题用短列表，不要使用 Markdown 表格；不要使用 # 或 ## 标题。"
            "来源编号只挂在关键概念、定义、定理、公式或课程原话后面，例如 [1][2]；不要每句话都挂。"
            "不要编造来源编号。"
            "如果资料没有提到：回答“当前上传课件未包含该细节，不要把未出现内容当成本轮复习重点。”"
            "不要猜测考试范围，不要说“期末不考”。"
            "遇到抽象概念时，可以用一句通俗类比或直观解释，但必须贴合资料内容，不要引入资料外例子。"
            "数学符号使用 Unicode 普通字符，不用 LaTeX 包裹，不输出反斜杠命令。"
            "例如 Q、Σ、δ、q0、×、→、F ⊆ Q、M = (Q, Σ, δ, q0, F)。"
        )
        temperature = 0.2
        user_prompt = (
            f"问题：{question}\n\n"
            f"课程资料片段：\n{context}\n\n"
            "请基于以上片段回答。要求：\n"
            "1. 第一句直接回答，不要客套开场，不要说“根据资料”。\n"
            "2. 语气像课程助教考前答疑：短段落优先，帮助理解和复习。\n"
            "3. 默认 300 到 600 字；简单概念题可以略短；复杂比较题、步骤题可接近 600 字。\n"
            "4. 比较题用短列表，不用 Markdown 表格，不用 # 或 ## 标题。\n"
            "5. 来源编号 [1][2] 只标在关键概念、定义、定理、公式或课程原话后面，不要每句话都标。\n"
            "6. 如果资料没有提到：回答“当前上传课件未包含该细节，不要把未出现内容当成本轮复习重点。”"
        )

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
            content += (
                "\n\n---\n"
                "*本次回答可能受单次输出长度限制影响，"
                "需继续展开可输入：继续生成下一部分。*"
            )
        return content
    except (KeyError, IndexError, ValueError) as exc:
        raise DeepSeekError(f"Unexpected {get_llm_provider_label()} response: {exc}") from exc
