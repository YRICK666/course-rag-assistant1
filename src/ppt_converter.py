from __future__ import annotations

import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path


class PptConversionError(RuntimeError):
    pass


class PptConversionUnavailable(PptConversionError):
    pass


@dataclass
class PptConversionResult:
    input_path: str
    output_path: str
    method: str
    message: str


def convert_with_powerpoint_com(input_path: Path, output_path: Path) -> PptConversionResult:
    try:
        import pythoncom
        import win32com.client
    except ImportError as exc:
        raise PptConversionUnavailable("未安装 pywin32，无法使用 PowerPoint COM 转换。") from exc

    presentation = None
    powerpoint = None
    pythoncom.CoInitialize()
    try:
        powerpoint = win32com.client.DispatchEx("PowerPoint.Application")
        powerpoint.Visible = 1
        presentation = powerpoint.Presentations.Open(str(input_path), True, False, False)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        presentation.SaveAs(str(output_path), 24)
        return PptConversionResult(
            input_path=str(input_path),
            output_path=str(output_path),
            method="powerpoint_com",
            message="已使用 Microsoft PowerPoint 转换为 PPTX。",
        )
    except Exception as exc:
        raise PptConversionError(f"PowerPoint COM 转换失败：{exc}") from exc
    finally:
        if presentation is not None:
            try:
                presentation.Close()
            except Exception:
                pass
        if powerpoint is not None:
            try:
                powerpoint.Quit()
            except Exception:
                pass
        pythoncom.CoUninitialize()


def find_libreoffice_executable() -> str | None:
    soffice = shutil.which("soffice")
    if soffice:
        return soffice

    common_path = Path(r"C:\Program Files\LibreOffice\program\soffice.exe")
    if common_path.exists():
        return str(common_path)

    libreoffice = shutil.which("libreoffice")
    if libreoffice:
        return libreoffice
    return None


def convert_with_libreoffice(input_path: Path, output_path: Path) -> PptConversionResult:
    executable = find_libreoffice_executable()
    if not executable:
        raise PptConversionUnavailable("未检测到 soffice，也未找到常见路径中的 LibreOffice。")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=str(output_path.parent)) as temp_dir:
        temp_input = Path(temp_dir) / input_path.name
        temp_output_dir = Path(temp_dir) / "converted"
        temp_output_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(input_path, temp_input)
        command = [
            executable,
            "--headless",
            "--convert-to",
            "pptx",
            "--outdir",
            str(temp_output_dir),
            str(temp_input),
        ]
        result = subprocess.run(command, capture_output=True, text=True, timeout=180)
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "").strip()
            raise PptConversionError(f"LibreOffice 转换失败：{detail or '未知错误'}")

        generated = temp_output_dir / f"{temp_input.stem}.pptx"
        if not generated.exists():
            candidates = list(temp_output_dir.glob("*.pptx"))
            if candidates:
                generated = candidates[0]
            else:
                raise PptConversionError("LibreOffice 转换结束，但未找到生成的 PPTX 文件。")

        shutil.move(str(generated), str(output_path))

    return PptConversionResult(
        input_path=str(input_path),
        output_path=str(output_path),
        method="libreoffice",
        message="已使用 LibreOffice 转换为 PPTX。",
    )


def convert_ppt_to_pptx(input_path: str | Path, output_path: str | Path) -> PptConversionResult:
    input_path = Path(input_path).resolve()
    output_path = Path(output_path).resolve()
    if input_path.suffix.lower() != ".ppt":
        raise ValueError("只支持将 .ppt 文件转换为 .pptx。")
    if not input_path.exists():
        raise FileNotFoundError(f"PPT 文件不存在：{input_path}")

    errors: list[str] = []
    try:
        return convert_with_libreoffice(input_path, output_path)
    except PptConversionUnavailable as exc:
        errors.append(str(exc))
    except PptConversionError:
        raise

    try:
        return convert_with_powerpoint_com(input_path, output_path)
    except (PptConversionUnavailable, PptConversionError) as exc:
        errors.append(str(exc))

    message = (
        "当前环境无法自动转换 .ppt，请安装 LibreOffice，或先使用 PowerPoint/WPS 将 .ppt 另存为 .pptx 后再上传。"
        f" 详细原因：{'；'.join(errors)}"
    )
    raise PptConversionUnavailable(message)
