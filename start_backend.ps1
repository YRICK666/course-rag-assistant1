param(
    [switch]$NoReload
)

$ErrorActionPreference = "Stop"

Set-Location -LiteralPath $PSScriptRoot

$Python = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $Python)) {
    throw "没有找到项目虚拟环境 Python：$Python"
}

$Arguments = @(
    "-m",
    "uvicorn",
    "backend.app.main:app",
    "--host",
    "127.0.0.1",
    "--port",
    "8000"
)

if (-not $NoReload) {
    $Arguments += @(
        "--reload",
        "--reload-dir",
        "backend",
        "--reload-dir",
        "src"
    )
}

Write-Host ""
Write-Host "正在启动课程资料 RAG 后端……"

if ($NoReload) {
    Write-Host "模式：稳定运行，不启用自动重载"
}
else {
    Write-Host "模式：开发模式，仅监控 backend 和 src"
}

Write-Host "地址：http://127.0.0.1:8000"
Write-Host ""

& $Python @Arguments

exit $LASTEXITCODE
