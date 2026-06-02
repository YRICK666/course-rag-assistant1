from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SUBJECTS_DIR = PROJECT_ROOT / "subjects"
DEFAULT_SUBJECT = "形式语言与自动机"
MIGRATION_MARKER = SUBJECTS_DIR / ".migration_done.json"

LEGACY_MATERIALS_DIR = PROJECT_ROOT / "materials"
LEGACY_OUTPUTS_DIR = PROJECT_ROOT / "outputs"


@dataclass(frozen=True)
class SubjectPaths:
    name: str
    root: Path
    materials_dir: Path
    outputs_dir: Path
    chroma_dir: Path
    extracted_text_dir: Path
    chapter_summaries_path: Path
    deleted_materials_dir: Path
    archived_original_ppt_dir: Path


def normalize_subject_name(name: str) -> str:
    normalized = " ".join(name.strip().split())
    if not normalized:
        raise ValueError("科目名称不能为空。")
    if any(separator in normalized for separator in ("/", "\\")):
        raise ValueError("科目名称不能包含路径分隔符。")
    if normalized in {".", ".."} or ".." in normalized:
        raise ValueError("科目名称不能包含 '..'。")
    return normalized


def get_subject_paths(name: str) -> SubjectPaths:
    subject_name = normalize_subject_name(name)
    root = SUBJECTS_DIR / subject_name
    outputs_dir = root / "outputs"
    return SubjectPaths(
        name=subject_name,
        root=root,
        materials_dir=root / "materials",
        outputs_dir=outputs_dir,
        chroma_dir=outputs_dir / "chroma_db",
        extracted_text_dir=outputs_dir / "extracted_text",
        chapter_summaries_path=outputs_dir / "chapter_summaries.json",
        deleted_materials_dir=outputs_dir / "deleted_materials",
        archived_original_ppt_dir=outputs_dir / "archived_original_ppt",
    )


def ensure_subject_structure(name: str) -> SubjectPaths:
    paths = get_subject_paths(name)
    paths.materials_dir.mkdir(parents=True, exist_ok=True)
    paths.outputs_dir.mkdir(parents=True, exist_ok=True)
    paths.chroma_dir.mkdir(parents=True, exist_ok=True)
    paths.extracted_text_dir.mkdir(parents=True, exist_ok=True)
    paths.deleted_materials_dir.mkdir(parents=True, exist_ok=True)
    paths.archived_original_ppt_dir.mkdir(parents=True, exist_ok=True)
    return paths


def create_subject(name: str) -> SubjectPaths:
    return ensure_subject_structure(name)


def list_subjects(auto_migrate: bool = True) -> list[str]:
    SUBJECTS_DIR.mkdir(parents=True, exist_ok=True)
    if auto_migrate:
        migrate_legacy_project_if_needed()
    subjects = [
        path.name
        for path in SUBJECTS_DIR.iterdir()
        if path.is_dir() and not path.name.startswith(".")
    ]
    return sorted(subjects, key=lambda item: (item != DEFAULT_SUBJECT, item.casefold()))


def copy_directory_contents(source: Path, target: Path) -> int:
    if not source.exists() or not source.is_dir():
        return 0

    copied = 0
    target.mkdir(parents=True, exist_ok=True)
    for item in source.iterdir():
        destination = target / item.name
        if item.is_dir():
            shutil.copytree(item, destination, dirs_exist_ok=True)
        elif item.is_file():
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, destination)
        copied += 1
    return copied


def write_migration_marker(status: dict) -> None:
    SUBJECTS_DIR.mkdir(parents=True, exist_ok=True)
    with MIGRATION_MARKER.open("w", encoding="utf-8") as file:
        json.dump(status, file, ensure_ascii=False, indent=2)


def migrate_legacy_project_if_needed() -> dict:
    SUBJECTS_DIR.mkdir(parents=True, exist_ok=True)
    default_paths = get_subject_paths(DEFAULT_SUBJECT)

    if MIGRATION_MARKER.exists():
        return {
            "migrated": False,
            "reason": "migration marker exists",
            "subject": DEFAULT_SUBJECT,
            "subject_root": str(default_paths.root),
        }

    if default_paths.root.exists():
        write_migration_marker(
            {
                "migrated": False,
                "reason": "default subject already exists",
                "subject": DEFAULT_SUBJECT,
                "subject_root": str(default_paths.root),
                "timestamp": datetime.now().isoformat(timespec="seconds"),
            }
        )
        return {
            "migrated": False,
            "reason": "default subject already exists",
            "subject": DEFAULT_SUBJECT,
            "subject_root": str(default_paths.root),
        }

    legacy_exists = LEGACY_MATERIALS_DIR.exists() or LEGACY_OUTPUTS_DIR.exists()
    if not legacy_exists:
        ensure_subject_structure(DEFAULT_SUBJECT)
        write_migration_marker(
            {
                "migrated": False,
                "reason": "legacy materials/outputs not found",
                "subject": DEFAULT_SUBJECT,
                "subject_root": str(default_paths.root),
                "timestamp": datetime.now().isoformat(timespec="seconds"),
            }
        )
        return {
            "migrated": False,
            "reason": "legacy materials/outputs not found",
            "subject": DEFAULT_SUBJECT,
            "subject_root": str(default_paths.root),
        }

    ensure_subject_structure(DEFAULT_SUBJECT)
    copied = {
        "materials": copy_directory_contents(LEGACY_MATERIALS_DIR, default_paths.materials_dir),
        "chroma_db": copy_directory_contents(
            LEGACY_OUTPUTS_DIR / "chroma_db",
            default_paths.chroma_dir,
        ),
        "extracted_text": copy_directory_contents(
            LEGACY_OUTPUTS_DIR / "extracted_text",
            default_paths.extracted_text_dir,
        ),
        "chapter_summaries": 0,
    }

    legacy_summaries = LEGACY_OUTPUTS_DIR / "chapter_summaries.json"
    if legacy_summaries.exists():
        default_paths.chapter_summaries_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(legacy_summaries, default_paths.chapter_summaries_path)
        copied["chapter_summaries"] = 1

    status = {
        "migrated": True,
        "reason": "legacy materials/outputs copied",
        "subject": DEFAULT_SUBJECT,
        "subject_root": str(default_paths.root),
        "copied": copied,
        "timestamp": datetime.now().isoformat(timespec="seconds"),
    }
    write_migration_marker(status)
    return status


def main() -> None:
    status = migrate_legacy_project_if_needed()
    print(json.dumps(status, ensure_ascii=False, indent=2))
    print(json.dumps({"subjects": list_subjects()}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
