from __future__ import annotations

import argparse
import json
import shutil
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable, Protocol

from ppt_converter import PptConversionError, PptConversionUnavailable, convert_ppt_to_pptx
from retriever import detect_chapter_number
from subject_store import SubjectPaths, get_subject_paths


SUPPORTED_MATERIAL_EXTENSIONS = {".pptx", ".ppt", ".pdf", ".docx", ".txt"}
PPT_CONVERSION_FRIENDLY_MESSAGE = (
    "当前环境无法自动转换 .ppt，请安装 LibreOffice，或安装 PowerPoint/pywin32，"
    "或先使用 PowerPoint/WPS 将 .ppt 手动另存为 .pptx 后再上传。"
)
PPT_PENDING_MESSAGE = "待转换，重建知识库时将自动转换。"


class UploadedFileLike(Protocol):
    name: str

    def getbuffer(self): ...


@dataclass
class MaterialInfo:
    file_name: str
    relative_path: str
    file_type: str
    size_bytes: int
    chapter: int | None
    modified_time: str
    conversion_status: str
    converted_pptx: str | None


def infer_chapter_from_filename(file_name: str) -> int | None:
    return detect_chapter_number(file_name)


def ensure_material_manager_dirs(subject_paths: SubjectPaths) -> None:
    subject_paths.materials_dir.mkdir(parents=True, exist_ok=True)
    subject_paths.deleted_materials_dir.mkdir(parents=True, exist_ok=True)
    subject_paths.archived_original_ppt_dir.mkdir(parents=True, exist_ok=True)


def validate_supported_extension(file_name: str) -> str:
    suffix = Path(file_name).suffix.lower()
    if suffix not in SUPPORTED_MATERIAL_EXTENSIONS:
        supported = ", ".join(sorted(SUPPORTED_MATERIAL_EXTENSIONS))
        raise ValueError(f"不支持的资料格式：{suffix or '无扩展名'}。支持格式：{supported}")
    return suffix


def safe_relative_path(file_name: str) -> Path:
    path = Path(file_name)
    if path.is_absolute():
        raise ValueError("文件名不能是绝对路径。")
    if any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError("文件名不能包含空路径、'.' 或 '..'。")
    return path


def resolve_material_path(subject_paths: SubjectPaths, file_name: str) -> Path:
    relative_path = safe_relative_path(file_name)
    target = (subject_paths.materials_dir / relative_path).resolve()
    materials_root = subject_paths.materials_dir.resolve()
    if target != materials_root and materials_root not in target.parents:
        raise ValueError("文件路径必须位于当前科目的 materials 目录内。")
    return target


def unique_path(path: Path) -> Path:
    if not path.exists():
        return path

    counter = 1
    while True:
        candidate = path.with_name(f"{path.stem}_{counter}{path.suffix}")
        if not candidate.exists():
            return candidate
        counter += 1


def find_converted_pptx(path: Path, materials_dir: Path) -> Path | None:
    if path.suffix.lower() != ".ppt":
        return None
    candidates = sorted(path.parent.glob(f"{path.stem}*.pptx"))
    for candidate in candidates:
        if candidate.is_file() and materials_dir.resolve() in candidate.resolve().parents:
            return candidate
    return None


def material_info(path: Path, materials_dir: Path) -> MaterialInfo:
    stat = path.stat()
    relative_path = path.relative_to(materials_dir).as_posix()
    converted_pptx = find_converted_pptx(path, materials_dir)
    conversion_status = ""
    converted_relative_path: str | None = None
    if path.suffix.lower() == ".ppt":
        if converted_pptx:
            conversion_status = "已转换"
            converted_relative_path = converted_pptx.relative_to(materials_dir).as_posix()
        else:
            conversion_status = "待转换"

    return MaterialInfo(
        file_name=path.name,
        relative_path=relative_path,
        file_type=path.suffix.lower(),
        size_bytes=stat.st_size,
        chapter=infer_chapter_from_filename(path.name),
        modified_time=datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
        conversion_status=conversion_status,
        converted_pptx=converted_relative_path,
    )


def list_materials(subject_paths: SubjectPaths) -> list[dict]:
    ensure_material_manager_dirs(subject_paths)
    files = [
        path
        for path in subject_paths.materials_dir.rglob("*")
        if path.is_file() and path.suffix.lower() in SUPPORTED_MATERIAL_EXTENSIONS
    ]
    files = sorted(
        files,
        key=lambda path: (
            infer_chapter_from_filename(path.name) or 999,
            path.suffix.lower(),
            path.name.casefold(),
        ),
    )
    return [asdict(material_info(path, subject_paths.materials_dir)) for path in files]


def uploaded_file_bytes(uploaded_file) -> bytes:
    if hasattr(uploaded_file, "getbuffer"):
        return bytes(uploaded_file.getbuffer())
    if hasattr(uploaded_file, "read"):
        return uploaded_file.read()
    if isinstance(uploaded_file, bytes):
        return uploaded_file
    raise TypeError("uploaded_file 必须是 Streamlit UploadedFile、类文件对象或 bytes。")


def uploaded_file_name(uploaded_file, index: int) -> str:
    name = getattr(uploaded_file, "name", None)
    if name:
        return Path(str(name)).name
    raise ValueError(f"第 {index} 个上传文件缺少文件名。")


def save_uploaded_files(subject_paths: SubjectPaths, uploaded_files: Iterable) -> list[dict]:
    ensure_material_manager_dirs(subject_paths)
    saved_files: list[dict] = []

    for index, uploaded_file in enumerate(uploaded_files, start=1):
        original_name = uploaded_file_name(uploaded_file, index)
        validate_supported_extension(original_name)

        target = unique_path(subject_paths.materials_dir / original_name)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(uploaded_file_bytes(uploaded_file))

        info = asdict(material_info(target, subject_paths.materials_dir))
        info["original_name"] = original_name
        info["renamed"] = target.name != original_name
        info["status"] = "uploaded"
        info["conversion_message"] = ""
        if target.suffix.lower() == ".ppt":
            info["status"] = "pending_conversion"
            info["conversion_message"] = PPT_PENDING_MESSAGE
        saved_files.append(info)

    return saved_files


def try_convert_ppt_material(subject_paths: SubjectPaths, file_name: str) -> dict:
    try:
        return convert_ppt_material(subject_paths, file_name)
    except (PptConversionError, OSError, ValueError, FileNotFoundError) as exc:
        return {
            "success": False,
            "file_name": file_name,
            "message": f"转换失败，原始 PPT 已保留。{exc}",
            "friendly_message": PPT_CONVERSION_FRIENDLY_MESSAGE,
        }


def archive_original_ppt(subject_paths: SubjectPaths, source: Path) -> Path:
    subject_paths.archived_original_ppt_dir.mkdir(parents=True, exist_ok=True)
    target = unique_path(subject_paths.archived_original_ppt_dir / source.name)
    shutil.move(str(source), str(target))
    return target


def convert_ppt_material(subject_paths: SubjectPaths, file_name: str) -> dict:
    ensure_material_manager_dirs(subject_paths)
    source = resolve_material_path(subject_paths, file_name)
    if not source.exists() or not source.is_file():
        raise FileNotFoundError(f"资料不存在：{file_name}")
    if source.suffix.lower() != ".ppt":
        raise ValueError("只有 .ppt 文件需要转换。")

    existing = find_converted_pptx(source, subject_paths.materials_dir)
    if existing:
        archived = archive_original_ppt(subject_paths, source)
        return {
            "success": True,
            "file_name": source.relative_to(subject_paths.materials_dir).as_posix(),
            "converted_pptx": existing.relative_to(subject_paths.materials_dir).as_posix(),
            "archived_original_ppt": archived.relative_to(subject_paths.archived_original_ppt_dir).as_posix(),
            "method": "existing",
            "message": "已转换为 PPTX，原始 PPT 已归档。",
        }

    target = unique_path(source.with_suffix(".pptx"))
    result = convert_ppt_to_pptx(source, target)
    archived = archive_original_ppt(subject_paths, source)
    return {
        "success": True,
        "file_name": source.relative_to(subject_paths.materials_dir).as_posix(),
        "converted_pptx": target.relative_to(subject_paths.materials_dir).as_posix(),
        "archived_original_ppt": archived.relative_to(subject_paths.archived_original_ppt_dir).as_posix(),
        "method": result.method,
        "message": "已转换为 PPTX，原始 PPT 已归档。",
    }


def batch_convert_ppt_materials(subject_paths: SubjectPaths) -> dict:
    ensure_material_manager_dirs(subject_paths)
    materials = list_materials(subject_paths)
    targets = [
        material["relative_path"]
        for material in materials
        if material["file_type"] == ".ppt"
    ]

    successes: list[dict] = []
    failures: list[dict] = []
    for relative_path in targets:
        result = try_convert_ppt_material(subject_paths, relative_path)
        if result.get("success"):
            successes.append(result)
        else:
            failures.append(result)

    return {
        "total": len(targets),
        "success_count": len(successes),
        "failure_count": len(failures),
        "successes": successes,
        "failures": failures,
    }


def soft_delete_material(subject_paths: SubjectPaths, file_name: str) -> dict:
    ensure_material_manager_dirs(subject_paths)
    source = resolve_material_path(subject_paths, file_name)
    if not source.exists() or not source.is_file():
        raise FileNotFoundError(f"资料不存在：{file_name}")
    validate_supported_extension(source.name)

    relative_path = source.relative_to(subject_paths.materials_dir)
    target = unique_path(subject_paths.deleted_materials_dir / relative_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(source), str(target))

    return {
        "file_name": source.name,
        "from": str(source),
        "to": str(target),
        "deleted_relative_path": target.relative_to(subject_paths.deleted_materials_dir).as_posix(),
    }


def rename_material(subject_paths: SubjectPaths, old_name: str, new_name: str) -> dict:
    ensure_material_manager_dirs(subject_paths)
    source = resolve_material_path(subject_paths, old_name)
    if not source.exists() or not source.is_file():
        raise FileNotFoundError(f"资料不存在：{old_name}")

    validate_supported_extension(source.name)
    validate_supported_extension(new_name)

    new_relative_path = safe_relative_path(new_name)
    target = (subject_paths.materials_dir / new_relative_path).resolve()
    materials_root = subject_paths.materials_dir.resolve()
    if target != materials_root and materials_root not in target.parents:
        raise ValueError("新文件路径必须位于当前科目的 materials 目录内。")
    if target.exists():
        raise FileExistsError(f"目标文件已存在：{new_name}")

    target.parent.mkdir(parents=True, exist_ok=True)
    source.rename(target)

    info = asdict(material_info(target, subject_paths.materials_dir))
    info["old_name"] = old_name
    return info


def material_stats(subject_paths: SubjectPaths) -> dict:
    materials = list_materials(subject_paths)
    total_size = sum(int(item["size_bytes"]) for item in materials)
    by_type: dict[str, int] = {}
    chapters: set[int] = set()

    for item in materials:
        file_type = str(item["file_type"])
        by_type[file_type] = by_type.get(file_type, 0) + 1
        chapter = item.get("chapter")
        if isinstance(chapter, int):
            chapters.add(chapter)

    return {
        "file_count": len(materials),
        "total_size_bytes": total_size,
        "by_type": dict(sorted(by_type.items())),
        "chapters": sorted(chapters),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Inspect subject materials without modifying files.")
    parser.add_argument("--subject", default="形式语言与自动机", help="Subject name to inspect.")
    parser.add_argument("--json", action="store_true", help="Print JSON output.")
    args = parser.parse_args()

    subject_paths = get_subject_paths(args.subject)
    output = {
        "subject": subject_paths.name,
        "materials_dir": str(subject_paths.materials_dir),
        "stats": material_stats(subject_paths),
        "materials": list_materials(subject_paths),
    }

    if args.json:
        print(json.dumps(output, ensure_ascii=False, indent=2))
        return

    print(f"科目：{output['subject']}")
    print(f"资料目录：{output['materials_dir']}")
    print(json.dumps(output["stats"], ensure_ascii=False, indent=2))
    for material in output["materials"]:
        chapter = material["chapter"] if material["chapter"] is not None else "未知"
        print(
            f"- {material['relative_path']} | {material['file_type']} | "
            f"{material['size_bytes']} bytes | 第{chapter}章"
        )


if __name__ == "__main__":
    main()
