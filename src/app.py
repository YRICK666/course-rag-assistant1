from __future__ import annotations

from html import escape
import re
import sys
from pathlib import Path

import streamlit as st

CURRENT_DIR = Path(__file__).resolve().parent
if str(CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_DIR))

from llm_deepseek import is_deepseek_configured
from retriever import (
    DEFAULT_TOP_K,
    clean_math_text,
    collection_count,
    get_embedding_model_name,
    list_indexed_sources,
)
from services.index_service import build_selected_materials_index, rebuild_all_materials_index
from services.learning_service import (
    generate_all_materials_overview,
    generate_current_material_overview,
    generate_study_guide,
    get_study_guide_cache_status,
)
from services.material_service import (
    SUPPORTED_MATERIAL_EXTENSIONS,
    batch_convert_subject_ppt_materials,
    convert_subject_ppt_material,
    get_material_options_for_build,
    get_subject_material_stats,
    get_subject_materials,
    material_display_info,
    material_navigation_rows,
    rename_subject_material,
    save_uploaded_materials,
    soft_delete_subject_material,
)
from services.qa_service import answer_source_references, ask_course_question, validate_question_request
from services.scope_service import (
    SCOPE_ALL,
    SCOPE_GROUP,
    SCOPE_MULTI,
    SCOPE_SINGLE,
    current_range_indexed_gap,
    grouped_materials,
    indexed_source_path_set,
    infer_material_category,
    material_build_status,
    resolve_scope_selection,
    unindexed_sources,
)
from subject_store import DEFAULT_SUBJECT, create_subject, ensure_subject_structure, list_subjects


PAGE_LEARN = "学习助手"
PAGE_MATERIALS = "资料管理"
PAGE_SETTINGS = "参数设置"
ALL_SOURCES = "全部资料"
UPLOAD_TYPES = [suffix.lstrip(".") for suffix in sorted(SUPPORTED_MATERIAL_EXTENSIONS)]


def inject_styles() -> None:
    st.markdown(
        """
        <style>
        :root {
            --app-bg: #f6f8fb;
            --card-bg: #ffffff;
            --ink: #0f172a;
            --muted: #64748b;
            --line: #e2e8f0;
            --brand: #4f7cff;
            --brand-dark: #315bdc;
            --brand-soft: #eef4ff;
            --danger: #dc2626;
        }
        .stApp {
            background: var(--app-bg);
            color: var(--ink);
        }
        .block-container {
            padding-top: 1.1rem;
            padding-bottom: 3rem;
            max-width: 1180px;
        }
        h2, h3 {
            letter-spacing: 0;
        }
        h3 {
            font-size: 1.28rem !important;
            line-height: 1.35 !important;
            margin-top: 1.1rem !important;
            margin-bottom: 0.55rem !important;
        }
        div[data-testid="stMetric"] {
            background: var(--card-bg);
            border: 1px solid var(--line);
            border-radius: 12px;
            padding: 0.55rem 0.7rem;
            box-shadow: 0 6px 18px rgba(15, 23, 42, 0.035);
        }
        div[data-testid="stMetric"] label {
            color: var(--muted) !important;
            font-size: 0.76rem !important;
        }
        div[data-testid="stMetricValue"] {
            font-size: 1.18rem !important;
            line-height: 1.25 !important;
        }
        div[data-testid="stVerticalBlockBorderWrapper"] {
            background: var(--card-bg);
            border-color: #e5e7eb !important;
            border-radius: 16px;
            box-shadow: 0 8px 22px rgba(0, 0, 0, 0.04);
        }
        .hero {
            padding: 0.1rem 0 0.35rem 0;
            margin-bottom: 0.25rem;
        }
        .hero h1 {
            color: var(--ink);
            font-size: 1.45rem;
            line-height: 1.2;
            font-weight: 700;
            margin: 0 0 0.18rem 0;
        }
        .hero p {
            color: #475569;
            font-size: 0.9rem;
            margin: 0;
        }
        .pill-row {
            display: flex;
            gap: 0.42rem;
            flex-wrap: wrap;
            margin: 0.1rem 0 0.8rem 0;
        }
        .status-pill {
            display: inline-flex;
            align-items: center;
            max-width: 100%;
            min-height: 1.72rem;
            padding: 0.28rem 0.58rem;
            border: 1px solid #dbe3ef;
            border-radius: 999px;
            background: rgba(255, 255, 255, 0.85);
            color: #1e293b;
            font-size: 0.8rem;
            line-height: 1.25;
            box-shadow: 0 4px 12px rgba(0, 0, 0, 0.025);
        }
        .status-pill span {
            color: var(--muted);
            margin-right: 0.25rem;
        }
        .mini-card-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
            gap: 0.65rem;
            margin: 0.25rem 0 0.9rem 0;
        }
        .mini-card {
            min-height: 3.3rem;
            background: var(--card-bg);
            border: 1px solid var(--line);
            border-radius: 13px;
            padding: 0.62rem 0.75rem;
            box-shadow: 0 7px 20px rgba(15, 23, 42, 0.04);
            overflow-wrap: anywhere;
        }
        .mini-card-label {
            color: var(--muted);
            font-size: 0.76rem;
            line-height: 1.25;
            margin-bottom: 0.18rem;
        }
        .mini-card-value {
            color: var(--ink);
            font-size: 0.95rem;
            font-weight: 650;
            line-height: 1.28;
        }
        .soft-note {
            color: var(--muted);
            font-size: 0.84rem;
            line-height: 1.55;
        }
        .section-subtitle {
            color: var(--muted);
            font-size: 0.88rem;
            line-height: 1.55;
            margin-top: -0.25rem;
            margin-bottom: 0.75rem;
        }
        .stButton > button {
            border-radius: 8px !important;
            padding: 0.32rem 0.64rem !important;
            min-height: 2rem !important;
            font-size: 0.88rem !important;
            font-weight: 600 !important;
            border-color: #cbd5e1 !important;
        }
        .stButton > button:hover {
            border-color: #94a3b8 !important;
            box-shadow: 0 4px 10px rgba(0, 0, 0, 0.035);
        }
        .stButton > button[kind="primary"] {
            background: var(--brand) !important;
            border-color: var(--brand) !important;
            color: #ffffff !important;
        }
        .stButton > button[kind="primary"]:hover {
            background: var(--brand-dark) !important;
            border-color: var(--brand-dark) !important;
            box-shadow: 0 6px 14px rgba(79, 124, 255, 0.16);
        }
        div[data-testid="stTabs"] [data-baseweb="tab-list"] {
            gap: 0.35rem;
            border-bottom: 1px solid #e5e7eb;
        }
        div[data-testid="stTabs"] button[data-baseweb="tab"] {
            height: 2.25rem;
            padding: 0 0.75rem;
            font-size: 0.9rem;
            border-radius: 8px 8px 0 0;
        }
        div[data-testid="stTabs"] [data-testid="stVerticalBlock"] {
            gap: 0.55rem;
        }
        section[data-testid="stSidebar"] {
            background: #f8fafc;
            border-right: 1px solid #e2e8f0;
        }
        section[data-testid="stSidebar"] h3 {
            font-size: 1rem !important;
            margin-bottom: 0.35rem !important;
        }
        section[data-testid="stSidebar"] .sidebar-section-label {
            color: #334155;
            font-size: 0.78rem;
            font-weight: 700;
            letter-spacing: 0;
            margin: 0.15rem 0 0.25rem 0;
        }
        section[data-testid="stSidebar"] div[role="radiogroup"] {
            gap: 0.35rem !important;
        }
        section[data-testid="stSidebar"] label[data-baseweb="radio"] {
            margin-right: 0.2rem !important;
        }
        section[data-testid="stSidebar"] .stButton > button {
            width: 100%;
            min-height: 1.88rem !important;
            padding: 0.22rem 0.5rem !important;
            font-size: 0.8rem !important;
        }
        section[data-testid="stSidebar"] [data-testid="stVerticalBlock"] {
            gap: 0.42rem;
        }
        section[data-testid="stSidebar"] hr {
            margin: 0.55rem 0 !important;
        }
        div[data-testid="stTextArea"] textarea {
            border-radius: 10px !important;
            font-size: 0.92rem !important;
            line-height: 1.55 !important;
        }
        div[data-testid="stChatInput"] textarea {
            min-height: 2.35rem !important;
            font-size: 0.9rem !important;
        }
        div[data-testid="stChatMessage"] {
            background: #ffffff;
            border: 1px solid #e5e7eb;
            border-radius: 14px;
            padding: 0.65rem 0.8rem;
            box-shadow: 0 6px 18px rgba(0, 0, 0, 0.035);
        }
        div[data-testid="stTextInput"] input,
        div[data-testid="stNumberInput"] input,
        div[data-testid="stSelectbox"] div {
            font-size: 0.9rem !important;
        }
        .element-container p, .stMarkdown p, .stMarkdown li {
            font-size: 0.92rem;
            line-height: 1.62;
        }
        .stMarkdown h4 {
            font-size: 1rem !important;
            margin-top: 0.2rem !important;
            margin-bottom: 0.45rem !important;
        }
        div[data-testid="stFileUploader"] {
            font-size: 0.88rem;
        }
        .nav-meta {
            color: #64748b;
            font-size: 0.76rem;
            line-height: 1.35;
            margin: -0.25rem 0 0.35rem 0.15rem;
            overflow-wrap: anywhere;
        }
        .status-dot {
            display: inline-block;
            width: 0.48rem;
            height: 0.48rem;
            border-radius: 999px;
            margin-right: 0.3rem;
            vertical-align: 0.02rem;
        }
        .status-indexed { background: #16a34a; }
        .status-unindexed { background: #ca8a04; }
        .status-pending { background: #ea580c; }
        .status-selected { color: #315bdc; font-weight: 700; }
        </style>
        """,
        unsafe_allow_html=True,
    )


def init_state(subjects: list[str]) -> None:
    defaults = {
        "current_page": PAGE_LEARN,
        "current_subject": subjects[0] if subjects else DEFAULT_SUBJECT,
        "selected_source": ALL_SOURCES,
        "materials_changed": False,
        "pending_delete": None,
        "pending_rename": None,
        "info_material": None,
        "last_build_summary": None,
        "last_answer": None,
        "last_hits": [],
        "last_query_warning": None,
        "last_answer_mode": None,
        "last_rewritten_query": None,
        "last_study_guide": None,
        "last_study_guide_sources": [],
        "last_study_guide_warning": None,
        "last_study_guide_signature": None,
        "last_study_guide_cache_key": None,
        "qa_question": "",
        "is_querying": False,
        "notice": None,
        "uploader_key_version": 0,
        "build_scope": "当前资料",
        "manual_build_files": [],
        "source_scope_mode": SCOPE_ALL,
        "single_source": "",
        "multi_sources": [],
        "source_group": "",
        "source_groups": [],
        "top_k": DEFAULT_TOP_K,
        "chunk_size": 900,
        "overlap": 120,
        "batch_size": 32,
        "embedding_model": get_embedding_model_name(),
        "use_deepseek": True,
    }
    for key, value in defaults.items():
        st.session_state.setdefault(key, value)

    if st.session_state["current_page"] not in {PAGE_LEARN, PAGE_MATERIALS, PAGE_SETTINGS}:
        st.session_state["current_page"] = PAGE_LEARN
    if subjects and st.session_state["current_subject"] not in subjects:
        st.session_state["current_subject"] = subjects[0]
        st.session_state["selected_source"] = ALL_SOURCES
        clear_answer_state()


def clear_answer_state() -> None:
    st.session_state["last_answer"] = None
    st.session_state["last_hits"] = []
    st.session_state["last_query_warning"] = None
    st.session_state["last_answer_mode"] = None
    st.session_state["last_rewritten_query"] = None


def clear_study_guide_state() -> None:
    st.session_state["last_study_guide"] = None
    st.session_state["last_study_guide_sources"] = []
    st.session_state["last_study_guide_warning"] = None
    st.session_state["last_study_guide_signature"] = None
    st.session_state["last_study_guide_cache_key"] = None


def scoped_state_key(base: str, subject_name: str | None = None) -> str:
    subject = subject_name or st.session_state.get("current_subject", DEFAULT_SUBJECT)
    return f"{base}::{subject}"


def clear_scope_state(subject_name: str | None = None) -> None:
    subject = subject_name or st.session_state.get("current_subject", DEFAULT_SUBJECT)
    for base in ("source_scope_mode", "single_source", "multi_sources", "source_group", "source_groups"):
        st.session_state.pop(scoped_state_key(base, subject), None)
    st.session_state.pop(scoped_state_key("previous_scope_mode", subject), None)
    st.session_state["selected_source"] = ALL_SOURCES
    st.session_state["source_scope_mode"] = SCOPE_ALL
    st.session_state["single_source"] = ""
    st.session_state["multi_sources"] = []
    st.session_state["source_group"] = ""
    st.session_state["source_groups"] = []
    clear_study_guide_state()


def set_notice(kind: str, message: str) -> None:
    st.session_state["notice"] = {"kind": kind, "message": message}


def show_notice() -> None:
    notice = st.session_state.pop("notice", None)
    if not notice:
        return
    kind = notice.get("kind", "info")
    message = str(notice.get("message", ""))
    if kind == "success":
        st.success(message)
        st.toast(message)
    elif kind == "warning":
        st.warning(message)
    elif kind == "error":
        st.error(message)
    else:
        st.info(message)


def format_size(size_bytes: int) -> str:
    value = float(size_bytes)
    for unit in ["B", "KB", "MB", "GB"]:
        if value < 1024 or unit == "GB":
            return f"{int(value)} B" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} GB"


def render_status_pills(items: list[tuple[str, str]]) -> None:
    pills = []
    for label, value in items:
        pills.append(f'<div class="status-pill"><span>{escape(label)}：</span>{escape(str(value))}</div>')
    st.markdown(f'<div class="pill-row">{"".join(pills)}</div>', unsafe_allow_html=True)


def render_mini_cards(items: list[tuple[str, str]]) -> None:
    cards = []
    for label, value in items:
        cards.append(
            '<div class="mini-card">'
            f'<div class="mini-card-label">{escape(label)}</div>'
            f'<div class="mini-card-value">{escape(str(value))}</div>'
            "</div>"
        )
    st.markdown(f'<div class="mini-card-grid">{"".join(cards)}</div>', unsafe_allow_html=True)


def scope_summary(scope_label: str, selected_sources: list[str], scope_mode: str) -> str:
    if scope_mode == SCOPE_ALL:
        return "全部资料"
    if scope_mode == SCOPE_GROUP:
        group_count = len(st.session_state.get("source_groups", []))
        if group_count:
            return f"{group_count} 个分组，{len(selected_sources)} 个资料"
    if selected_sources:
        return f"{len(selected_sources)} 个资料"
    label = scope_label.replace("当前范围：", "").strip()
    return label if label and label != "全部资料" else "未选择资料"


def source_title(selected_source: str) -> str:
    if selected_source == ALL_SOURCES:
        return "全部课程资料"
    return Path(selected_source).stem


def short_text(text: str, max_chars: int = 34) -> str:
    text = str(text)
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 3] + "..."


def status_dot_class(status_key: str) -> str:
    return {
        "indexed": "status-indexed",
        "pending": "status-pending",
        "unindexed": "status-unindexed",
    }.get(status_key, "status-unindexed")


def render_nav_meta(material: dict, indexed_paths: set[str], selected: bool = False) -> None:
    status, status_key = material_build_status(material, indexed_paths)
    category = infer_material_category(material["relative_path"])
    selected_text = " · 当前选中" if selected else ""
    selected_class = " status-selected" if selected else ""
    st.markdown(
        '<div class="nav-meta">'
        f'<span class="status-dot {status_dot_class(status_key)}"></span>'
        f'<span class="{selected_class.strip()}">{escape(status)}</span>'
        f' · {escape(category)} · {escape(material["file_type"])}{escape(selected_text)}'
        "</div>",
        unsafe_allow_html=True,
    )


def selection_table(
    materials: list[dict],
    indexed_paths: set[str],
    selected_defaults: list[str],
    key: str,
) -> list[str]:
    rows = []
    default_set = set(selected_defaults)
    for material in materials:
        status, _ = material_build_status(material, indexed_paths)
        relative_path = material["relative_path"]
        rows.append(
            {
                "选择": relative_path in default_set,
                "文件名": short_text(relative_path, 46),
                "类型": material["file_type"],
                "分类": infer_material_category(relative_path),
                "建库状态": status,
                "路径": relative_path,
            }
        )

    edited_rows = st.data_editor(
        rows,
        key=key,
        hide_index=True,
        use_container_width=True,
        height=260,
        column_config={
            "选择": st.column_config.CheckboxColumn("选择", width="small"),
            "文件名": st.column_config.TextColumn("文件名", width="large"),
            "类型": st.column_config.TextColumn("类型", width="small"),
            "分类": st.column_config.TextColumn("分类", width="small"),
            "建库状态": st.column_config.TextColumn("建库状态", width="medium"),
            "路径": None,
        },
        disabled=["文件名", "类型", "分类", "建库状态", "路径"],
    )
    selected = [row["路径"] for row in edited_rows if row.get("选择")]
    if selected:
        details = "；".join(selected[:4])
        more = f" 等 {len(selected)} 个文件" if len(selected) > 4 else ""
        st.caption(f"已选择：{details}{more}")
    return selected


def record_build_result(result: dict) -> dict:
    st.session_state["last_build_summary"] = result
    st.session_state["materials_changed"] = False
    clear_answer_state()
    clear_study_guide_state()
    set_notice(result.get("notice_kind", "success"), result.get("message", "建库完成。"))
    return result


def record_material_result(result: dict, *, clear_answers: bool = False) -> dict:
    if result.get("materials_changed"):
        st.session_state["materials_changed"] = True
    if clear_answers:
        clear_answer_state()
    set_notice(result.get("notice_kind", "success"), result.get("message", "操作完成。"))
    return result


def rebuild_current_subject(subject_paths) -> dict:
    result = rebuild_all_materials_index(
        subject_paths,
        chunk_size=int(st.session_state["chunk_size"]),
        overlap=int(st.session_state["overlap"]),
        batch_size=int(st.session_state["batch_size"]),
        embedding_model=str(st.session_state["embedding_model"]),
    )
    return record_build_result(result)


def run_selected_build(subject_paths, selected_files: list[str], *, reset: bool, scope_label: str) -> dict:
    result = build_selected_materials_index(
        subject_paths,
        selected_files,
        reset=reset,
        scope_label=scope_label,
        chunk_size=int(st.session_state["chunk_size"]),
        overlap=int(st.session_state["overlap"]),
        batch_size=int(st.session_state["batch_size"]),
        embedding_model=str(st.session_state["embedding_model"]),
    )
    return record_build_result(result)


def scope_build_label(scope_label: str, *, reset: bool, all_scope: bool = False) -> str:
    if all_scope:
        action = "重建" if reset else "添加/更新"
        suffix = "知识库" if reset else "到知识库"
        return f"{action}全部资料{suffix}"
    label = scope_label.replace("当前范围：", "")
    label = re.sub(r"（.*?）", "", label).strip()
    if not label or label == "未选择资料":
        label = "当前范围"
    action = "重建" if reset else "添加/更新当前范围"
    suffix = "知识库" if reset else "到知识库"
    return f"{action}{suffix}" if not reset else f"{action}当前范围知识库"


def render_scope_build_actions(
    subject_paths,
    *,
    scope_label: str,
    selected_sources: list[str],
    all_scope: bool = False,
) -> None:
    build_files = selected_sources
    if all_scope:
        build_files = [material["relative_path"] for material in get_material_options_for_build(subject_paths)]

    if not build_files:
        return

    st.info("当前范围包含未建库资料，可先建立该范围的知识库。")
    update_col, build_col = st.columns(2)
    update_col.caption("添加/更新：推荐，保留已有知识库，只补充当前范围资料。")
    if update_col.button(scope_build_label(scope_label, reset=False, all_scope=all_scope), type="primary"):
        try:
            spinner_text = "正在添加/更新全部资料到知识库..." if all_scope else "正在添加/更新当前范围到知识库..."
            with st.spinner(spinner_text):
                run_selected_build(
                    subject_paths,
                    build_files,
                    reset=False,
                    scope_label="全部资料" if all_scope else scope_label,
                )
            st.session_state["current_page"] = PAGE_LEARN
        except Exception as exc:
            set_notice("error", f"添加/更新当前范围失败：{exc}")
        st.rerun()
    build_col.caption("重建：会清空当前知识库，只保留当前范围资料。")
    if build_col.button(scope_build_label(scope_label, reset=True, all_scope=all_scope)):
        try:
            spinner_text = "正在重建全部资料知识库..." if all_scope else "正在重建当前范围知识库..."
            with st.spinner(spinner_text):
                run_selected_build(
                    subject_paths,
                    build_files,
                    reset=True,
                    scope_label="全部资料" if all_scope else scope_label,
                )
            st.session_state["current_page"] = PAGE_LEARN
        except Exception as exc:
            set_notice("error", f"重建当前范围失败：{exc}")
        st.rerun()


def current_scope_selection(materials: list[dict]) -> tuple[str, list[str], str]:
    mode = st.session_state.get(scoped_state_key("source_scope_mode"), st.session_state.get("source_scope_mode", SCOPE_ALL))
    return resolve_scope_selection(
        materials,
        mode=mode,
        single_source=st.session_state.get(scoped_state_key("single_source"), ""),
        multi_sources=st.session_state.get(scoped_state_key("multi_sources"), []),
        source_groups=st.session_state.get(scoped_state_key("source_groups"), []),
    )


def render_parameter_controls() -> None:
    st.slider(
        "检索片段数量 top-k",
        min_value=1,
        max_value=20,
        key="top_k",
        help="控制每次回答参考多少条资料，数值越大参考越多，但可能更慢。",
    )
    st.number_input(
        "切分长度",
        min_value=300,
        max_value=2000,
        step=100,
        key="chunk_size",
        help="控制资料被切成多长的文本片段，通常不需要修改。",
    )
    st.number_input(
        "重叠长度",
        min_value=0,
        max_value=500,
        step=20,
        key="overlap",
        help="控制相邻片段重复多少内容，用于减少上下文断裂。",
    )
    st.number_input(
        "建库 batch size",
        min_value=4,
        max_value=128,
        step=4,
        key="batch_size",
        help="控制建库时每批处理多少文本片段，普通用户保持默认即可。",
    )
    st.text_input(
        "Embedding 模型",
        key="embedding_model",
        help="用于把文本转成向量，普通学生通常不需要修改。",
    )
    st.checkbox(
        "DeepSeek 开关",
        key="use_deepseek",
        help="开启后答案更自然，但会消耗 API 额度；未配置 API key 时会自动使用本地回退。",
    )


def render_sidebar(
    subjects: list[str],
    subject_paths,
    stats: dict,
    indexed_count: int,
    source_options: list[str],
    source_lookup: dict[str, str],
    materials: list[dict],
    indexed_paths: set[str],
) -> None:
    with st.sidebar:
        st.markdown('<div class="sidebar-section-label">科目</div>', unsafe_allow_html=True)
        subject_index = subjects.index(st.session_state["current_subject"]) if st.session_state["current_subject"] in subjects else 0
        selected_subject = st.selectbox("选择科目", subjects, index=subject_index, label_visibility="collapsed")
        if selected_subject != st.session_state["current_subject"]:
            old_subject = st.session_state["current_subject"]
            clear_scope_state(old_subject)
            st.session_state["current_subject"] = selected_subject
            clear_scope_state(selected_subject)
            st.session_state["pending_delete"] = None
            st.session_state["pending_rename"] = None
            clear_answer_state()
            st.rerun()

        with st.expander("新建科目", expanded=False):
            new_subject = st.text_input("科目名称", placeholder="例如：软件工程")
            if st.button("创建科目", use_container_width=True):
                try:
                    created = create_subject(new_subject)
                except ValueError as exc:
                    set_notice("error", str(exc))
                else:
                    st.session_state["current_subject"] = created.name
                    clear_scope_state(created.name)
                    st.session_state["current_page"] = PAGE_MATERIALS
                    clear_answer_state()
                    set_notice("success", f"已创建科目：{created.name}")
                st.rerun()

        status_text = "已建库" if indexed_count > 0 else "未建库"
        st.caption(f"资料数量：{stats['file_count']} 个")
        st.caption(f"知识库状态：{status_text}，{indexed_count} 个文本块")

        st.divider()
        st.markdown('<div class="sidebar-section-label">资料范围</div>', unsafe_allow_html=True)
        available_paths = [material["relative_path"] for material in materials]
        mode_key = scoped_state_key("source_scope_mode", subject_paths.name)
        single_key = scoped_state_key("single_source", subject_paths.name)
        multi_key = scoped_state_key("multi_sources", subject_paths.name)
        groups_key = scoped_state_key("source_groups", subject_paths.name)
        previous_mode_key = scoped_state_key("previous_scope_mode", subject_paths.name)

        st.session_state.setdefault(mode_key, SCOPE_ALL)
        if st.session_state.get(single_key) not in available_paths:
            st.session_state[single_key] = available_paths[0] if available_paths else ""
        st.session_state[multi_key] = [
            source for source in st.session_state.get(multi_key, []) if source in set(available_paths)
        ]

        scope_mode = st.radio(
            "范围模式",
            [SCOPE_ALL, SCOPE_SINGLE, SCOPE_MULTI, SCOPE_GROUP],
            key=mode_key,
            horizontal=True,
            label_visibility="collapsed",
        )
        if st.session_state.get(previous_mode_key) != scope_mode:
            if st.session_state.get(previous_mode_key) is not None:
                st.session_state[multi_key] = []
                st.session_state[groups_key] = []
            st.session_state[previous_mode_key] = scope_mode
            clear_answer_state()

        if scope_mode == SCOPE_SINGLE:
            if available_paths:
                st.selectbox(
                    "选择资料",
                    available_paths,
                    index=available_paths.index(st.session_state[single_key]) if st.session_state[single_key] in available_paths else 0,
                    key=single_key,
                    format_func=lambda value: short_text(value, 30),
                    label_visibility="collapsed",
                )
            else:
                st.info("当前没有可选择的资料。")
        elif scope_mode == SCOPE_MULTI:
            st.multiselect(
                "选择多个资料",
                available_paths,
                key=multi_key,
                format_func=lambda value: short_text(value, 30),
                help="可同时选择多个文件，问答和概览只使用这些资料。",
                label_visibility="collapsed",
            )
        elif scope_mode == SCOPE_GROUP:
            groups = grouped_materials(materials)
            group_options = list(groups)
            st.session_state[groups_key] = [
                group for group in st.session_state.get(groups_key, []) if group in group_options
            ]
            if group_options:
                st.multiselect(
                    "选择一个或多个分组",
                    group_options,
                    key=groups_key,
                    help="可同时选择第1章和第2章等多个分组，问答会限定在这些分组的资料中。",
                    label_visibility="collapsed",
                )
            else:
                st.info("当前没有可选择的分组。")

        st.session_state["source_scope_mode"] = scope_mode
        st.session_state["single_source"] = st.session_state.get(single_key, "")
        st.session_state["multi_sources"] = st.session_state.get(multi_key, [])
        st.session_state["source_groups"] = st.session_state.get(groups_key, [])

        scope_label, selected_sources, _ = current_scope_selection(materials)
        st.caption(scope_label)
        if selected_sources:
            with st.expander("查看当前选中资料", expanded=False):
                for source in selected_sources:
                    material = next((item for item in materials if item["relative_path"] == source), None)
                    if material:
                        render_nav_meta(material, indexed_paths, selected=True)
                    st.write(source)
        elif scope_mode != SCOPE_ALL:
            st.warning("当前没有选中资料。")

        if materials:
            with st.expander("查看资料状态", expanded=False):
                for material in materials:
                    st.write(short_text(material["relative_path"], 30))
                    render_nav_meta(
                        material,
                        indexed_paths,
                        selected=material["relative_path"] in selected_sources,
                    )

        st.caption("问答、资料概览和“当前选择资料”建库都会使用这个范围。")

        st.divider()
        st.markdown('<div class="sidebar-section-label">导航</div>', unsafe_allow_html=True)
        nav_cols = st.columns(3, gap="small")
        if nav_cols[0].button("学习助手", use_container_width=True):
            st.session_state["current_page"] = PAGE_LEARN
            st.rerun()
        if nav_cols[1].button("资料管理", use_container_width=True):
            st.session_state["current_page"] = PAGE_MATERIALS
            st.rerun()
        if nav_cols[2].button("参数设置", use_container_width=True):
            st.session_state["current_page"] = PAGE_SETTINGS
            st.rerun()

        with st.expander("知识库维护", expanded=False):
            if st.button("重建当前科目知识库", use_container_width=True):
                try:
                    with st.spinner("正在重建当前科目知识库..."):
                        rebuild_current_subject(subject_paths)
                except Exception as exc:
                    set_notice("error", f"重建失败：{exc}")
                st.rerun()

            if st.button("刷新资料列表", use_container_width=True):
                set_notice("success", "资料列表已刷新。")
                st.rerun()

        if st.session_state.get("materials_changed"):
            st.warning("当前科目资料已变化，请重建知识库。")


def render_hero(
    subject_name: str,
    scope_label: str,
    indexed_count: int,
    selected_sources: list[str],
    scope_mode: str,
) -> None:
    st.markdown(
        """
        <div class="hero">
            <h1>AI 学习助手</h1>
            <p>围绕当前课程资料生成概览、复习提纲与可追溯问答。</p>
        </div>
        """,
        unsafe_allow_html=True,
    )
    deepseek_text = "已配置" if is_deepseek_configured() else "未配置，本地回退"
    render_status_pills(
        [
            ("当前科目", subject_name),
            ("当前范围", scope_summary(scope_label, selected_sources, scope_mode)),
            ("知识库", f"{indexed_count} 块"),
            ("DeepSeek", deepseek_text),
        ]
    )


def render_overview_sources(sources: list[dict[str, str]], include_text: bool = False) -> None:
    if not sources:
        return
    with st.expander("概览来源", expanded=False):
        for source in sources:
            st.markdown(f"[{source['rank']}] {source['label']}")
            if source.get("source_path"):
                st.caption(source["source_path"])
            if include_text and source.get("text"):
                st.write(clean_math_text(source["text"]))


def render_study_guide_sources(sources: list[dict[str, str]]) -> None:
    if not sources:
        return
    with st.expander("复习提纲来源", expanded=False):
        for source in sources:
            st.markdown(f"[{source['rank']}] {source['label']}")
            if source.get("source_path"):
                st.caption(source["source_path"])
            if source.get("text"):
                st.write(clean_math_text(source["text"]))


def render_study_guide_card(
    subject_paths,
    scope_label: str,
    selected_sources: list[str],
    indexed_count: int,
    indexed_paths: set[str],
    show_heading: bool = True,
) -> None:
    if show_heading:
        st.markdown("### 复习辅助")
    with st.container(border=True):
        st.markdown("#### 复习提纲")
        range_sources, missing_sources, all_scope = current_range_indexed_gap(
            get_material_options_for_build(subject_paths),
            scope_label,
            selected_sources,
            indexed_paths,
        )
        guide_signature = (
            subject_paths.name,
            scope_label,
            tuple(selected_sources),
            tuple(sorted(indexed_paths)),
            indexed_count,
        )

        if indexed_count == 0:
            st.info("当前科目知识库为空。请先在“资料管理”中上传资料并重建知识库。")
            render_scope_build_actions(
                subject_paths,
                scope_label=scope_label,
                selected_sources=selected_sources,
                all_scope=all_scope,
            )
            return

        if not range_sources:
            st.info("当前范围没有可生成复习提纲的资料。")
            return

        if missing_sources:
            st.warning("当前范围包含未建库资料，请先添加/更新当前范围到知识库。")
            with st.expander("查看未建库资料", expanded=False):
                for source in missing_sources:
                    st.write(source)
            render_scope_build_actions(
                subject_paths,
                scope_label=scope_label,
                selected_sources=selected_sources,
                all_scope=all_scope,
            )
            return

        st.caption("将基于已建库资料生成结构化复习提纲。")
        guide_cache_status = get_study_guide_cache_status(
            subject_paths,
            selected_sources,
            use_deepseek=bool(st.session_state["use_deepseek"]),
        )
        guide_cache_key = guide_cache_status.get("cache_key")
        has_current_study_guide = (
            st.session_state.get("last_study_guide_signature") == guide_signature
            and bool(st.session_state.get("last_study_guide"))
        )
        if not has_current_study_guide and guide_cache_status.get("exists"):
            st.session_state["last_study_guide"] = guide_cache_status.get("content")
            st.session_state["last_study_guide_sources"] = guide_cache_status.get("sources") or []
            st.session_state["last_study_guide_warning"] = None
            st.session_state["last_study_guide_signature"] = guide_signature
            st.session_state["last_study_guide_cache_key"] = guide_cache_key
            has_current_study_guide = bool(st.session_state.get("last_study_guide"))
        button_label = "重新生成当前范围复习提纲" if has_current_study_guide else "生成当前范围复习提纲"
        generate_clicked = st.button(
            button_label,
            type="primary",
            key=f"study_guide_action_{guide_cache_key or 'current_range'}",
        )
        force_refresh = bool(generate_clicked and has_current_study_guide)

        if generate_clicked:
            with st.spinner("正在根据当前资料范围生成复习提纲..."):
                result = generate_study_guide(
                    subject_paths,
                    selected_sources,
                    use_deepseek=bool(st.session_state["use_deepseek"]),
                    force_refresh=force_refresh,
                )
            st.session_state["last_study_guide"] = result["content"]
            st.session_state["last_study_guide_sources"] = result["sources"]
            st.session_state["last_study_guide_warning"] = result["warning"]
            st.session_state["last_study_guide_signature"] = guide_signature
            st.session_state["last_study_guide_cache_key"] = result.get("cache_key") or guide_cache_key
            if result["cached"]:
                st.caption("已读取缓存的复习提纲。")
            st.rerun()

        if st.session_state.get("last_study_guide_signature") != guide_signature:
            return

        if st.session_state.get("last_study_guide_warning"):
            st.warning(st.session_state["last_study_guide_warning"])
        if st.session_state.get("last_study_guide"):
            with st.container(border=True):
                st.markdown("#### 提纲内容")
                st.markdown(clean_math_text(st.session_state["last_study_guide"]))
            render_study_guide_sources(st.session_state.get("last_study_guide_sources", []))


def render_overview_card(
    subject_paths,
    scope_label: str,
    selected_sources: list[str],
    indexed_count: int,
    indexed_paths: set[str],
    show_heading: bool = True,
) -> None:
    overview_heading = "所选资料概览" if selected_sources else "全课程资料概览"
    if len(selected_sources) == 1:
        overview_heading = "当前资料概览"
    if show_heading:
        st.markdown(f"### {overview_heading}")
    with st.container(border=True):
        st.markdown(f"#### {overview_heading}")
        if indexed_count == 0:
            st.info("当前科目知识库为空。请先在“资料管理”中上传资料并重建知识库。")
            render_scope_build_actions(
                subject_paths,
                scope_label=scope_label,
                selected_sources=selected_sources,
                all_scope=not selected_sources,
            )
            return

        if not selected_sources:
            st.info("请选择具体资料查看学习内容概览，或点击生成当前科目的全课程概览。")
            render_scope_build_actions(
                subject_paths,
                scope_label=scope_label,
                selected_sources=selected_sources,
                all_scope=True,
            )
            if st.button("生成全课程概览"):
                with st.spinner("正在生成全课程概览..."):
                    result = generate_all_materials_overview(
                        subject_paths,
                        use_deepseek=bool(st.session_state["use_deepseek"]),
                    )
                if result["warning"]:
                    st.warning(result["warning"])
                if result["cached"]:
                    st.caption("已读取缓存的概览。")
                st.markdown(clean_math_text(result["content"]))
                render_overview_sources(result["sources"])
            return

        missing_sources = unindexed_sources(selected_sources, indexed_paths)
        if missing_sources:
            st.warning("所选资料尚未进入知识库，请先重建所选资料知识库或添加/更新所选资料。")
            with st.expander("查看未建库资料", expanded=False):
                for source in missing_sources:
                    st.write(source)
            render_scope_build_actions(
                subject_paths,
                scope_label=scope_label,
                selected_sources=selected_sources,
            )
            return

        st.caption(f"{len(selected_sources)} 个资料")
        refresh_overview = st.button("重新生成资料概览")
        with st.spinner("正在生成资料概览..."):
            result = generate_current_material_overview(
                subject_paths,
                selected_sources,
                use_deepseek=bool(st.session_state["use_deepseek"]),
                force_refresh=refresh_overview,
            )
        if result["warning"]:
            st.warning(result["warning"])
        if result["cached"]:
            st.caption("已读取缓存的资料概览。")
        st.markdown(clean_math_text(result["content"]))
        render_overview_sources(result["sources"], include_text=True)


def render_answer_sources(hits: list[dict]) -> None:
    with st.expander("查看来源引用", expanded=False):
        references = answer_source_references(hits)
        if not references:
            st.info("没有找到相关来源。")
            return
        for reference in references:
            st.markdown(f"**{reference['title']}**")
            if reference.get("source_path"):
                st.caption(reference["source_path"])
            st.write(clean_math_text(reference["text"]))
            st.divider()


def render_learning_page(
    subject_paths,
    scope_label: str,
    selected_sources: list[str],
    indexed_count: int,
    indexed_paths: set[str],
) -> None:
    if selected_sources:
        with st.expander("查看完整选中资料列表", expanded=False):
            for source in selected_sources:
                st.write(source)

    overview_tab, study_guide_tab = st.tabs(["资料概览", "复习提纲"])
    with overview_tab:
        render_overview_card(
            subject_paths,
            scope_label,
            selected_sources,
            indexed_count,
            indexed_paths,
            show_heading=False,
        )
    with study_guide_tab:
        render_study_guide_card(
            subject_paths,
            scope_label,
            selected_sources,
            indexed_count,
            indexed_paths,
            show_heading=False,
        )
    if selected_sources and unindexed_sources(selected_sources, indexed_paths):
        return

    deepseek_answer_enabled = bool(st.session_state["use_deepseek"] and is_deepseek_configured())
    answer_mode_text = (
        "DeepSeek 增强回答。DeepSeek 已启用：答案由 DeepSeek 基于课程资料生成。"
        if deepseek_answer_enabled
        else "本地回退回答。DeepSeek 未配置或已关闭：使用本地回退答案。"
    )
    st.markdown("### 智能问答")
    st.caption("回答将基于当前资料范围生成，并尽量显示来源。")
    with st.container(border=True):
        st.markdown("#### 向课程资料提问")
        st.caption(f"当前回答模式：{answer_mode_text}")

        if not st.session_state.get("qa_question") and not st.session_state.get("last_answer"):
            st.info("你可以询问：这一部分主要讲了什么？有哪些重点概念？")

        if st.session_state.get("qa_question"):
            with st.chat_message("user"):
                st.markdown(str(st.session_state["qa_question"]))

        if st.session_state.get("last_query_warning"):
            with st.chat_message("assistant"):
                st.warning(st.session_state["last_query_warning"])

        if st.session_state.get("last_answer"):
            with st.chat_message("assistant"):
                st.markdown(clean_math_text(st.session_state["last_answer"]))
                if st.session_state.get("last_hits"):
                    render_answer_sources(st.session_state["last_hits"])
                if st.session_state.get("last_answer_mode"):
                    st.caption(f"回答模式：{st.session_state['last_answer_mode']}")
                if st.session_state.get("last_rewritten_query"):
                    st.caption(f"检索改写：{st.session_state['last_rewritten_query']}")

        submitted_question = st.chat_input(
            "输入你的问题",
            disabled=bool(st.session_state.get("is_querying")),
            key="qa_chat_input",
        )

        if submitted_question:
            question = str(submitted_question).strip()
            st.session_state["qa_question"] = question
            validation_error = validate_question_request(
                subject_paths,
                question=question,
                selected_sources=selected_sources,
                indexed_paths=indexed_paths,
            )
            if validation_error:
                clear_answer_state()
                st.session_state["qa_question"] = question
                st.session_state["last_query_warning"] = validation_error["error"]
                st.session_state["last_answer_mode"] = "DeepSeek 增强回答" if deepseek_answer_enabled else "本地回退回答"
                if validation_error.get("missing_sources"):
                    render_scope_build_actions(
                        subject_paths,
                        scope_label=scope_label,
                        selected_sources=selected_sources,
                    )
                st.rerun()
            else:
                st.session_state["is_querying"] = True
                clear_answer_state()
                st.session_state["qa_question"] = question
                try:
                    with st.chat_message("assistant"):
                        with st.spinner("正在检索当前资料范围并生成答案..."):
                            result = ask_course_question(
                                subject_paths,
                                question=question,
                                top_k=int(st.session_state["top_k"]),
                                use_deepseek=bool(st.session_state["use_deepseek"]),
                                selected_sources=selected_sources,
                                indexed_paths=indexed_paths,
                            )
                    if result["success"]:
                        st.session_state["last_answer"] = result["answer"]
                        st.session_state["last_hits"] = result["hits"]
                        st.session_state["last_query_warning"] = result["warning"]
                        st.session_state["last_answer_mode"] = result.get("answer_mode")
                        st.session_state["last_rewritten_query"] = result.get("rewritten_query")
                    else:
                        st.session_state["last_query_warning"] = result["error"]
                        st.session_state["last_answer_mode"] = result.get("answer_mode")
                finally:
                    st.session_state["is_querying"] = False
                st.rerun()


def render_material_row(subject_paths, material: dict, index: int, indexed_paths: set[str]) -> None:
    display_material = material_display_info(material, indexed_paths)
    relative_path = display_material["relative_path"]
    category = display_material["category"]
    build_status = display_material["build_status"]
    conversion_status = display_material["conversion_status_display"]
    row = st.columns([3.0, 0.7, 0.85, 1.0, 1.05, 0.9, 2.7])
    row[0].markdown(f"**{relative_path}**")
    row[1].write(display_material["file_type"])
    row[2].write(format_size(int(display_material["size_bytes"])))
    row[3].write(category)
    row[4].write(build_status)
    row[5].write(conversion_status)
    action_count = 4 if display_material["file_type"] == ".ppt" else 3
    action_cols = row[6].columns(action_count)
    if action_cols[0].button("查看", key=f"info_{index}_{relative_path}"):
        st.session_state["info_material"] = relative_path
        st.session_state["current_page"] = PAGE_MATERIALS
        st.rerun()
    if action_cols[1].button("改名", key=f"rename_{index}_{relative_path}"):
        st.session_state["pending_rename"] = relative_path
        st.session_state["current_page"] = PAGE_MATERIALS
        st.rerun()
    if action_cols[2].button("移除", key=f"delete_{index}_{relative_path}"):
        st.session_state["pending_delete"] = relative_path
        st.session_state["current_page"] = PAGE_MATERIALS
        st.rerun()
    if display_material["file_type"] == ".ppt" and action_cols[3].button("转PPTX", key=f"convert_{index}_{relative_path}"):
        try:
            result = convert_subject_ppt_material(subject_paths, relative_path)
        except Exception as exc:
            set_notice("error", f"转换失败，原始 PPT 已保留。{exc}")
        else:
            record_material_result(result)
        st.session_state["current_page"] = PAGE_MATERIALS
        st.rerun()

    if st.session_state.get("info_material") == relative_path:
        extra = ""
        if display_material["file_type"] == ".ppt":
            extra = f"\n\n转换状态：{conversion_status}"
            if display_material.get("converted_pptx"):
                extra += f"　转换文件：{display_material['converted_pptx']}"
        st.info(
            f"文件：{relative_path}\n\n"
            f"类型：{display_material['file_type']}　大小：{format_size(int(display_material['size_bytes']))}　"
            f"推断分类：{category}　建库状态：{build_status}　修改时间：{display_material['modified_time']}"
            f"{extra}"
        )

    if st.session_state.get("pending_rename") == relative_path:
        with st.container(border=True):
            new_name = st.text_input("新文件名", value=relative_path, key=f"rename_input_{index}_{relative_path}")
            confirm_col, cancel_col = st.columns(2)
            if confirm_col.button("确认重命名", type="primary", key=f"confirm_rename_{index}_{relative_path}"):
                try:
                    result = rename_subject_material(subject_paths, relative_path, new_name)
                except Exception as exc:
                    set_notice("error", f"重命名失败：{exc}")
                else:
                    st.session_state["pending_rename"] = None
                    record_material_result(result, clear_answers=True)
                st.session_state["current_page"] = PAGE_MATERIALS
                st.rerun()
            if cancel_col.button("取消", key=f"cancel_rename_{index}_{relative_path}"):
                st.session_state["pending_rename"] = None
                st.session_state["current_page"] = PAGE_MATERIALS
                st.rerun()

    if st.session_state.get("pending_delete") == relative_path:
        with st.container(border=True):
            st.warning(f"确认移除资料：{relative_path}？文件会移动到当前科目的 outputs/deleted_materials，不会直接永久删除。")
            confirm_col, cancel_col = st.columns(2)
            if confirm_col.button("确认移除", type="primary", key=f"confirm_delete_{index}_{relative_path}"):
                try:
                    result = soft_delete_subject_material(subject_paths, relative_path)
                except Exception as exc:
                    set_notice("error", f"移除失败：{exc}")
                else:
                    st.session_state["pending_delete"] = None
                    record_material_result(result, clear_answers=True)
                st.session_state["current_page"] = PAGE_MATERIALS
                st.rerun()
            if cancel_col.button("取消", key=f"cancel_delete_{index}_{relative_path}"):
                st.session_state["pending_delete"] = None
                st.session_state["current_page"] = PAGE_MATERIALS
                st.rerun()


def render_materials_page(subject_paths, stats: dict, indexed_count: int) -> None:
    st.markdown("### 资料管理")
    render_mini_cards(
        [
            ("当前科目", subject_paths.name),
            ("资料文件", f"{stats['file_count']} 个"),
            ("资料大小", format_size(int(stats["total_size_bytes"]))),
            ("知识库", f"{indexed_count} 块"),
        ]
    )

    if st.session_state.get("materials_changed"):
        st.warning("资料文件已变化，请重建当前科目知识库后再查询最新内容。")

    indexed_paths = indexed_source_path_set(list_indexed_sources(outputs_dir=subject_paths.outputs_dir))
    build_materials = get_material_options_for_build(subject_paths)
    scope_label, current_scope_files, _ = current_scope_selection(build_materials)

    with st.expander("上传资料", expanded=False):
        upload_inner, upload_action = st.columns([3.5, 1])
        with upload_inner:
            uploader_key = f"material_uploader_{st.session_state['uploader_key_version']}"
            uploaded_files = st.file_uploader(
                "选择课程资料",
                type=UPLOAD_TYPES,
                accept_multiple_files=True,
                help="支持 PPT、PPTX、PDF、Word 和 TXT。PPT 会先保存原文件，重建知识库时自动转换为 PPTX。",
                key=uploader_key,
            )
        with upload_action:
            st.caption("上传是次级操作，保存后请按需建库。")
            if st.button("保存上传文件", type="primary", disabled=not uploaded_files):
                try:
                    result = save_uploaded_materials(subject_paths, uploaded_files or [])
                except Exception as exc:
                    set_notice("error", f"上传失败：{exc}")
                else:
                    record_material_result(result)
                    st.session_state["current_page"] = PAGE_MATERIALS
                    st.session_state["uploader_key_version"] += 1
                st.rerun()

    workspace_left, rebuild_col = st.columns([1.05, 1.35])
    with workspace_left:
        with st.container(border=True):
            st.markdown("#### 资料导航 / 文件列表")
            if not build_materials:
                st.info("当前科目还没有可建库资料。")
            else:
                nav_rows = [
                    {
                        "资料": short_text(row["relative_path"], 42),
                        "类型": row["file_type"],
                        "分类": row["category"],
                        "建库状态": row["build_status"],
                        "当前": "是" if row["selected"] else "",
                    }
                    for row in material_navigation_rows(build_materials, indexed_paths, current_scope_files)
                ]
                st.dataframe(nav_rows, hide_index=True, use_container_width=True, height=300)
                st.caption("左侧边栏可选择单个、多选或按分组选择资料；这里用于快速查看资料状态。")

    with rebuild_col:
        with st.container(border=True):
            st.markdown("#### 建库工作区")
            st.caption("只会处理当前科目的资料库，不影响其他科目。")

            if st.session_state.get("build_scope") == "当前章节/当前资料":
                st.session_state["build_scope"] = "当前资料"
            build_scope = st.radio(
                "建库范围",
                ["全部资料", "当前资料", "手动选择资料"],
                key="build_scope",
                horizontal=False,
            )
            current_selected_files = current_scope_files

            selected_files: list[str] = []
            scope_label = build_scope
            if build_scope == "全部资料":
                st.caption("全部重建：适合整门课复习，耗时较长。")
            elif build_scope == "当前资料":
                st.caption("所选重建：适合只复习当前资料范围，速度更快。")
                if current_selected_files:
                    selected_files = current_selected_files
                    if len(selected_files) == 1:
                        st.info(f"当前资料：{selected_files[0]}")
                    else:
                        st.info(f"{scope_label.replace('当前范围：', '当前选择：')}，将一起处理。")
                        st.caption("；".join(selected_files[:4]))
                else:
                    st.warning("当前范围是“全部资料”，请先在左侧选择具体资料。")
            else:
                st.caption("添加/更新：适合逐章补充知识库。")
                selected_files = selection_table(
                    build_materials,
                    indexed_paths,
                    [item for item in st.session_state.get("manual_build_files", []) if item in {m["relative_path"] for m in build_materials}],
                    key=f"manual_build_table_{subject_paths.name}",
                )
                st.session_state["manual_build_files"] = selected_files
                if not build_materials:
                    st.info("当前没有可选择的资料。")

            st.caption("全部重建：适合整门课复习，耗时较长。")
            if st.button("重建全部资料知识库", type="primary", use_container_width=True):
                try:
                    with st.status("正在重建当前科目知识库...", expanded=True) as status:
                        st.write("正在转换并读取当前科目的全部 materials。")
                        summary = rebuild_current_subject(subject_paths)
                        status.update(label="知识库重建完成", state="complete")
                    st.session_state["last_build_summary"] = summary
                except Exception as exc:
                    set_notice("error", f"重建失败：{exc}")
                st.session_state["current_page"] = PAGE_MATERIALS
                st.rerun()

            selected_disabled = build_scope == "全部资料" or not selected_files
            st.caption("所选重建：适合只复习当前资料范围，速度更快。")
            if st.button("重建所选资料知识库", use_container_width=True, disabled=selected_disabled):
                try:
                    with st.status("正在重建所选资料知识库...", expanded=True) as status:
                        st.write("正在处理所选资料。")
                        summary = run_selected_build(
                            subject_paths,
                            selected_files,
                            reset=True,
                            scope_label=scope_label,
                        )
                        status.update(label="所选资料知识库重建完成", state="complete")
                    st.session_state["last_build_summary"] = summary
                except Exception as exc:
                    set_notice("error", f"重建所选资料失败：{exc}")
                st.session_state["current_page"] = PAGE_MATERIALS
                st.rerun()

            st.caption("添加/更新：适合逐章补充知识库。")
            if st.button("添加/更新所选资料到知识库", use_container_width=True, disabled=selected_disabled):
                try:
                    with st.status("正在添加/更新所选资料...", expanded=True) as status:
                        st.write("正在删除所选资料旧索引并写入新索引。")
                        summary = run_selected_build(
                            subject_paths,
                            selected_files,
                            reset=False,
                            scope_label=scope_label,
                        )
                        status.update(label="所选资料添加/更新完成", state="complete")
                    st.session_state["last_build_summary"] = summary
                except Exception as exc:
                    set_notice("error", f"添加/更新所选资料失败：{exc}")
                st.session_state["current_page"] = PAGE_MATERIALS
                st.rerun()

            summary = st.session_state.get("last_build_summary")
            if summary:
                conversion = summary.get("ppt_conversion") or {}
                st.caption(
                    f"最近重建：{summary.get('file_count', 0)} 个文件，"
                    f"{summary.get('chunk_count', 0)} 个文本块，"
                    f"{summary.get('chroma_count', 0)} 条 Chroma 记录。"
                )
                st.caption(
                    "本次范围："
                    + ("所选资料" if summary.get("scope") == "selected" else "全部资料")
                )
                indexed_files = summary.get("indexed_files") or []
                if indexed_files:
                    shown_files = "；".join(indexed_files[:4])
                    suffix = f" 等 {len(indexed_files)} 个文件" if len(indexed_files) > 4 else ""
                    st.caption(f"本次文件：{shown_files}{suffix}")
                st.caption(
                    f"PPT 转换：成功 {conversion.get('success_count', 0)} 个，"
                    f"失败 {conversion.get('failure_count', 0)} 个。"
                )
                if conversion.get("success_count", 0):
                    st.caption("已转换的原始 PPT 已归档。")
                if conversion.get("failures"):
                    with st.expander("PPT 转换失败原因", expanded=True):
                        for item in conversion["failures"]:
                            st.warning(f"{item.get('file_name')}：{item.get('message')}")
                if summary.get("chunk_count", 0) == 0:
                    st.warning("知识库还是 0 块，可能是转换失败或资料无法提取文本。")

            if st.button("批量转换 PPT", use_container_width=True):
                result = batch_convert_subject_ppt_materials(subject_paths)
                st.session_state["current_page"] = PAGE_MATERIALS
                record_material_result(result)
                st.rerun()

    file_tab, help_tab = st.tabs(["当前资料文件", "操作说明"])
    with file_tab:
        with st.container(border=True):
            materials = get_subject_materials(subject_paths)
            if not materials:
                st.info("当前科目还没有资料。请先上传 PPT、PDF、Word 或 TXT。")
            else:
                header = st.columns([3.0, 0.7, 0.85, 1.0, 1.05, 0.9, 2.7])
                header[0].caption("文件名")
                header[1].caption("类型")
                header[2].caption("大小")
                header[3].caption("分类")
                header[4].caption("建库")
                header[5].caption("转换")
                header[6].caption("操作")
                for index, material in enumerate(materials):
                    render_material_row(subject_paths, material, index, indexed_paths)
    with help_tab:
        st.info(
            "上传、移除或重命名资料后，请点击“重建当前科目知识库”。"
            "移除操作会把文件移动到当前科目的 outputs/deleted_materials，不会直接永久删除。"
            "已转换的原始 .ppt 会自动归档到 outputs/archived_original_ppt。"
        )


def render_settings_page(subject_paths, indexed_count: int) -> None:
    st.markdown("### 参数设置")
    st.markdown('<div class="section-subtitle">一般保持默认即可，只有在检索效果不理想时再调整。</div>', unsafe_allow_html=True)
    left, right = st.columns([1.1, 1])
    with left:
        with st.container(border=True):
            st.markdown("#### 高级参数")
            render_parameter_controls()
    with right:
        with st.container(border=True):
            st.markdown("#### 当前状态")
            render_mini_cards(
                [
                    ("当前科目", subject_paths.name),
                    ("知识库", f"{indexed_count} 块"),
                    ("DeepSeek", "已配置，可生成自然语言答案" if is_deepseek_configured() else "未配置，将使用本地检索摘要"),
                ]
            )
            st.caption(f"当前科目资料目录：{subject_paths.materials_dir}")
            st.caption(f"当前科目输出目录：{subject_paths.outputs_dir}")


st.set_page_config(page_title="课程资料智能学习助手", layout="wide")
inject_styles()

subjects = list_subjects()
init_state(subjects)
subject_paths = ensure_subject_structure(st.session_state["current_subject"])
stats = get_subject_material_stats(subject_paths)
indexed_count = collection_count(outputs_dir=subject_paths.outputs_dir)
indexed_sources = list_indexed_sources(outputs_dir=subject_paths.outputs_dir)
indexed_paths = indexed_source_path_set(indexed_sources)
materials_for_source_select = get_material_options_for_build(subject_paths)
source_lookup = {}
source_options = [ALL_SOURCES]

render_sidebar(
    subjects,
    subject_paths,
    stats,
    indexed_count,
    source_options,
    source_lookup,
    materials_for_source_select,
    indexed_paths,
)

scope_label, selected_sources, scope_mode = current_scope_selection(materials_for_source_select)
scope_signature = (subject_paths.name, scope_mode, tuple(selected_sources))
previous_scope_signature = st.session_state.get("active_scope_signature")
if previous_scope_signature is not None and previous_scope_signature != scope_signature:
    clear_answer_state()
    clear_study_guide_state()
st.session_state["active_scope_signature"] = scope_signature

render_hero(subject_paths.name, scope_label, indexed_count, selected_sources, scope_mode)
show_notice()

if st.session_state["current_page"] == PAGE_MATERIALS:
    render_materials_page(subject_paths, stats, indexed_count)
elif st.session_state["current_page"] == PAGE_SETTINGS:
    render_settings_page(subject_paths, indexed_count)
else:
    render_learning_page(subject_paths, scope_label, selected_sources, indexed_count, indexed_paths)
