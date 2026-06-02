from __future__ import annotations

from material_manager import (
    SUPPORTED_MATERIAL_EXTENSIONS,
    batch_convert_ppt_materials,
    convert_ppt_material,
    list_materials,
    material_stats,
    rename_material,
    save_uploaded_files,
    soft_delete_material,
)
from services.scope_service import infer_material_category, material_build_status


INDEXABLE_EXTENSIONS = {".pptx", ".pdf", ".docx", ".txt"}
SELECTABLE_BUILD_EXTENSIONS = INDEXABLE_EXTENSIONS | {".ppt"}


def get_subject_material_stats(subject_paths) -> dict:
    return material_stats(subject_paths)


def get_subject_materials(subject_paths) -> list[dict]:
    return list_materials(subject_paths)


def get_material_options_for_build(subject_paths) -> list[dict]:
    return [
        material
        for material in get_subject_materials(subject_paths)
        if material["file_type"] in SELECTABLE_BUILD_EXTENSIONS
    ]


def material_display_info(material: dict, indexed_paths: set[str]) -> dict:
    build_status, status_key = material_build_status(material, indexed_paths)
    relative_path = material["relative_path"]
    return {
        **material,
        "category": infer_material_category(relative_path),
        "build_status": build_status,
        "build_status_key": status_key,
        "conversion_status_display": material.get("conversion_status") or "-",
    }


def material_display_list(materials: list[dict], indexed_paths: set[str]) -> list[dict]:
    return [material_display_info(material, indexed_paths) for material in materials]


def material_navigation_rows(
    materials: list[dict],
    indexed_paths: set[str],
    current_scope_files: list[str],
) -> list[dict]:
    selected_set = set(current_scope_files)
    rows = []
    for material in material_display_list(materials, indexed_paths):
        rows.append(
            {
                "relative_path": material["relative_path"],
                "file_type": material["file_type"],
                "category": material["category"],
                "build_status": material["build_status"],
                "selected": material["relative_path"] in selected_set,
            }
        )
    return rows


def save_uploaded_materials(subject_paths, uploaded_files) -> dict:
    saved = save_uploaded_files(subject_paths, uploaded_files or [])
    renamed_count = sum(1 for item in saved if item.get("renamed"))
    pending_ppt_count = sum(
        1
        for item in saved
        if item.get("file_type") == ".ppt" and item.get("status") == "pending_conversion"
    )
    suffix = f"，其中 {renamed_count} 个重名文件已自动重命名" if renamed_count else ""
    if pending_ppt_count:
        suffix += f"，{pending_ppt_count} 个 PPT 待转换，重建知识库时将自动转换"
    return {
        "success": True,
        "saved": saved,
        "renamed_count": renamed_count,
        "pending_ppt_count": pending_ppt_count,
        "materials_changed": True,
        "notice_kind": "success",
        "message": f"已上传 {len(saved)} 个文件{suffix}，请重建知识库。",
    }


def rename_subject_material(subject_paths, old_name: str, new_name: str) -> dict:
    result = rename_material(subject_paths, old_name, new_name)
    return {
        "success": True,
        "result": result,
        "materials_changed": True,
        "notice_kind": "success",
        "message": "资料已重命名，请重建知识库。",
    }


def soft_delete_subject_material(subject_paths, relative_path: str) -> dict:
    result = soft_delete_material(subject_paths, relative_path)
    return {
        "success": True,
        "result": result,
        "materials_changed": True,
        "notice_kind": "success",
        "message": "资料已移除，请重建知识库。",
    }


def convert_subject_ppt_material(subject_paths, relative_path: str) -> dict:
    result = convert_ppt_material(subject_paths, relative_path)
    return {
        "success": True,
        "result": result,
        "materials_changed": True,
        "notice_kind": "success",
        "message": f"已转换为 PPTX，原始 PPT 已归档。生成文件：{result['converted_pptx']}。请重建知识库。",
    }


def conversion_failure_details(result: dict, limit: int = 3) -> str:
    return "；".join(
        f"{item.get('file_name')}: {item.get('message')}"
        for item in result.get("failures", [])[:limit]
    )


def batch_convert_subject_ppt_materials(subject_paths) -> dict:
    result = batch_convert_ppt_materials(subject_paths)
    if result["failure_count"]:
        details = conversion_failure_details(result)
        notice_kind = "warning"
        message = (
            f"批量转换完成：成功 {result['success_count']} 个，失败 {result['failure_count']} 个。"
            f"成功项的原始 PPT 已归档；失败项原始 PPT 已保留。{details}"
        )
    else:
        notice_kind = "success"
        message = (
            f"批量转换完成：成功 {result['success_count']} 个。"
            "已转换为 PPTX，原始 PPT 已归档。请重建知识库。"
        )

    return {
        "success": True,
        "result": result,
        "materials_changed": bool(result["success_count"]),
        "notice_kind": notice_kind,
        "message": message,
    }
