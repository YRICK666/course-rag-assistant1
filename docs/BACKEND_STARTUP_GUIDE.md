# 课程资料 RAG 查询项目：后端启动与启动速度说明

## 1. 问题结论

目前已经确认，后端启动慢并不是由之前的 Chroma 自动清理代码导致的。

检查结果：

- 项目中已没有 `chroma_cleanup_service`
- 项目中已没有 `cleanup_orphaned_chroma_data`
- 当前后端启动函数只执行：

```python
@app.on_event("startup")
def on_startup():
    init_db()
    ensure_qa_history_schema()
```

实测耗时：

```text
init_db: 约 0.016 秒
ensure_qa_history_schema: 约 0.001 秒
```

因此，数据库初始化本身不是启动慢的主要原因。

主要原因是原启动命令中的：

```powershell
--reload
```

Uvicorn 默认会监控整个项目根目录：

```text
G:\AI-Workstation\课程资料RAG查询
```

这会把以下目录和文件一起纳入监控：

- `backend`
- `src`
- `subjects`
- 各科目的 `outputs`
- ChromaDB 数据目录
- PDF、PPT、DOCX 等课程资料
- 各类备份文件

当项目中的资料、索引和输出文件较多时，WatchFiles 初始化和扫描会明显拖慢后端启动。

---

## 2. 推荐的后端启动命令

### 2.1 开发模式：只监控代码目录

推荐日常开发时使用：

```powershell
cd "G:\AI-Workstation\课程资料RAG查询"

& ".\.venv\Scripts\python.exe" `
    -m uvicorn backend.app.main:app `
    --reload `
    --reload-dir backend `
    --reload-dir src `
    --host 127.0.0.1 `
    --port 8000
```

该命令只监控：

```text
backend
src
```

不会再监控：

```text
subjects
outputs
chroma_db
课程资料文件
```

正常启动日志应类似：

```text
Will watch for changes in these directories:
['...\backend', '...\src']
```

---

### 2.2 稳定模式：关闭自动重载

展示、答辩或不需要实时修改代码时，建议使用：

```powershell
cd "G:\AI-Workstation\课程资料RAG查询"

& ".\.venv\Scripts\python.exe" `
    -m uvicorn backend.app.main:app `
    --host 127.0.0.1 `
    --port 8000
```

这种模式启动最快、最稳定。

注意：

- 修改 Python 代码后需要手动停止并重新启动后端
- 停止服务时按 `Ctrl + C`

---

## 3. 推荐创建统一启动脚本

建议在项目根目录创建：

```text
start_backend.ps1
```

脚本内容：

```powershell
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
```

---

## 4. 启动脚本使用方法

### 4.1 开发模式

```powershell
cd "G:\AI-Workstation\课程资料RAG查询"

.\start_backend.ps1
```

效果：

- 启用自动重载
- 只监控 `backend` 和 `src`
- 修改 Python 代码后自动重启
- 不扫描课程资料和 ChromaDB

### 4.2 稳定模式

```powershell
cd "G:\AI-Workstation\课程资料RAG查询"

.\start_backend.ps1 -NoReload
```

效果：

- 不启用自动重载
- 启动速度最快
- 更适合答辩、演示和正式使用

---

## 5. 一次性创建 `start_backend.ps1`

在项目根目录运行：

```powershell
cd "G:\AI-Workstation\课程资料RAG查询"

$ScriptPath = ".\start_backend.ps1"

$Content = @'
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
'@

Set-Content `
    -LiteralPath $ScriptPath `
    -Value $Content `
    -Encoding UTF8

Write-Host "已创建：$ScriptPath"
```

---

## 6. 前端启动命令

打开另一个 PowerShell 窗口：

```powershell
cd "G:\AI-Workstation\课程资料RAG查询\frontend"

npm run dev
```

前端通常运行在：

```text
http://127.0.0.1:5173
```

---

## 7. 推荐的日常启动方式

### 开发调试

后端：

```powershell
cd "G:\AI-Workstation\课程资料RAG查询"
.\start_backend.ps1
```

前端：

```powershell
cd "G:\AI-Workstation\课程资料RAG查询\frontend"
npm run dev
```

### 展示或答辩

后端：

```powershell
cd "G:\AI-Workstation\课程资料RAG查询"
.\start_backend.ps1 -NoReload
```

前端：

```powershell
cd "G:\AI-Workstation\课程资料RAG查询\frontend"
npm run dev
```

---

## 8. 注意事项

1. 不要再使用默认全目录监控命令：

```powershell
python -m uvicorn backend.app.main:app --reload --host 127.0.0.1 --port 8000
```

因为它会监控整个项目根目录。

2. 看到以下日志不代表数据库异常：

```text
Waiting for application startup.
Application startup complete.
```

这只是 Uvicorn 的正常启动流程。

3. 当前项目启动阶段没有自动清理 ChromaDB，也不会自动删除任何课程资料或索引。

4. 若启动仍然异常缓慢，优先使用：

```powershell
.\start_backend.ps1 -NoReload
```

如果该模式明显更快，说明慢点仍主要来自 WatchFiles 的目录监控。
