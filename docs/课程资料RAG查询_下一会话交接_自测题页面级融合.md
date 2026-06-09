# 课程资料 RAG 查询 — 下一会话交接文档

## 状态：已实现

本文档记录自测题页面级组卷（single_page）、页面融合出题（fusion）、E 编号来源预览、以及 PDF 导出的功能实现状态。

---

## 一、项目位置

```text
G:\AI-Workstation\课程资料RAG查询
```

前置文档（每次新会话必须先读）：

- `AGENTS.md` — AI 工作规则（权威）
- `CLAUDE.md` — 项目层级规则
- `docs/SELF_TEST_PAGE_LEVEL_GENERATION_IMPLEMENTATION_REPORT.md` — 页面级组卷详细实施报告

---

## 二、已修改文件

| 文件 | 修改内容 |
|---|---|
| `backend/app/main.py` | 新增 `SelfTestRequest.generation_mode` 字段；自测题路由透传 `generation_mode`；新增 `_build_docx_response()`、`_build_pdf_response()`；新增 `POST /api/export/self-test/docx`、`POST /api/export/document/docx`、`POST /api/export/document/pdf` |
| `backend/app/services/self_test_service.py` | **重写为页面级组卷**：`_build_page_units()`、`_merge_page_text()`、`_assign_page_evidence()`、页面蓝图标验、fusion 概念卡/关系识别/融合组构建、fallback 蓝图、页面级结果校验 |
| `frontend/src/App.tsx` | 自测题设置增加 `generationMode` 控制；E 编号点击来源预览（`handleSelfTestSourceClick`）；自测题来源区域展开/高亮/滚动；导出 PDF/Word 按钮 |
| `frontend/src/types.ts` | 新增 `QuizGenerationMode` 类型、`QuizSettings.generationMode` 字段；新增 `ExportDocumentRequest` 类型 |
| `frontend/src/api.ts` | 新增 `exportDocumentDocx()`、`exportDocumentPdf()` 函数 |
| `src/llm_deepseek.py` | 未修改；`post_chat_completions()` 已有重试机制（max_attempts=3，支持 SSL/Connection/Timeout/429/5xx 重试） |

---

## 三、single_page 最终流程

```
1. collect_chunks_by_scope() → 获取所有 Chunk
2. _build_page_units(chunks) → 按 source_path + 页码分组形成页面单元
3. _representative_pages(pages, target_count) → 页面级均衡抽样
4. _assign_page_evidence(pages) → 页面内轮询分配 E 编号
5. LLM 蓝图生成（页面级约束 prompt）
6. _validate_blueprint() → 页面级校验
7. 校验失败 → _fallback_blueprint() → 本地合法蓝图
8. LLM 正式出题
9. _validate_final_answer() → 页面级结果校验
10. 校验失败 → LLM 自动修复（最多一次）
11. 返回结果
```

---

## 四、fusion 最终流程

```
1-4. 同 single_page（建页面单元、抽样、分配 E 编号）
5. _extract_page_concept_cards() → LLM 提取页面概念卡
6. _validate_concept_cards() → 角色/概念/事实校验
7. _build_fusion_groups() → 页面关系评分 → 融合组
8. 无可靠融合组 → 降级 single_page
9. LLM 蓝图（fusion 约束 prompt，填空题 single_page，选择/简答融合）
10. _validate_blueprint() → 包含 fusion 特有校验
11. 校验失败 → _fallback_blueprint() → 本地蓝图（含 fusion 轮换）
12-14. 同 single_page
```

---

## 五、页面证据单元最终结构

```python
{
    "page_id": "P001",
    "page_key": "source_key|slide:1",
    "source_key": "source_path_normalized",
    "source_path": "相对路径",
    "file_name": "文件名",
    "file_type": "pdf/pptx",
    "location_type": "slide" | "page" | "chunk_fallback",
    "location_number": 1,
    "metadata_quality": "exact" | "fallback",
    "page_number": None,          # 仅 PDF
    "slide_number": 1,            # 仅 PPT
    "chunks": [...],              # 该页所有 Chunk
    "chunk_ids": [...],
    "evidence_ids": ["E1", "E2"],
    "text": "合并后的单页文本（≤2400 字符）",
}
```

---

## 六、蓝图字段

```json
{
    "questions": [
        {
            "number": 1,
            "type": "choice" | "fill" | "essay",
            "difficulty": "easy" | "medium" | "hard",
            "mode": "single_page" | "fusion",
            "page_ids": ["P001"],
            "relation": "定义与示例",
            "relation_type": "definition_example",
            "knowledge_point": "具体知识点",
            "objective": "具体考查目标",
            "evidence_ids": ["E1", "E2"]
        }
    ]
}
```

---

## 七、页面概念卡结构

```json
{
  "page_id": "P001",
  "concepts": ["核心概念名"],
  "role": "definition" | "condition" | "mechanism" | "process" | "comparison" | "example" | "application" | "conclusion",
  "key_facts": ["该页最重要的事实"],
  "prerequisites": [],
  "outcomes": []
}
```

---

## 八、融合组结构

```python
{
    "group_id": "G001",
    "relation_type": "definition_example",
    "relation": "定义与示例",
    "shared_concepts": ["核心概念"],
    "page_ids": ["P001", "P003"],
    "evidence_ids": ["E1", "E2", "E5", "E6"],
    "score": 13,
}
```

### 关系类型

| 关系类型 | 说明 |
|---|---|
| `definition_example` | 定义与示例 |
| `definition_application` | 定义与应用 |
| `condition_result` | 条件与结果 |
| `process_sequence` | 过程衔接 |
| `mechanism_application` | 原理与应用 |
| `theory_case` | 理论与案例 |
| `comparison` | 概念比较 |
| `cause_effect` | 条件与作用机制 |

---

## 九、程序校验规则

### 蓝图校验

- 题量、题型、顺序必须完全匹配
- `single_page` 题只能有一个 `page_id`
- `fusion` 题必须有 2～3 个 `page_id`
- `evidence_ids` 必须在有效范围内
- 每题证据不能跨越其未指定的页面
- fusion 题必须匹配校验过的关系组
- fusion 题证据必须覆盖全部融合页面
- 可用页面充足时不得重复使用页面
- 存在融合关系时至少包含一道融合题

### 最终结果校验

- 必须包含 `## 解析版` 等固定标题
- 题目标题序列必须与蓝图一致（`### 第N题｜题型`）
- 含〖依据〗的解析数量 ≥ 题目数
- 〖依据〗只能引用该题 `evidence_ids` 中的 E 编号
- `single_page` 题正式依据必须属于其唯一页面
- `fusion` 题正式依据必须覆盖全部融合页面
- 不允许引用未分配给该题的证据编号

---

## 十、Fallback / 降级规则

1. 概念卡提取失败 → 无概念卡 → 无融合组 → 降级 single_page
2. 模型蓝图未通过校验 → 使用 `_fallback_blueprint()`（本地合法蓝图）
3. fusion 蓝图仍按页面轮换，填空题 single_page，选择/简答融合
4. fallback 页面（缺失页码的 Chunk）不参与融合
5. 可用页面少于题目 → 均衡复用页面
6. 降级时 `warning` 会说明情况

---

## 十一、E 编号来源预览

- 正则 `/E(\d+)/gi` 匹配 E1、E2、[E1]、【E1】
- 点击后 `E1 → targetIndex = 0 → hits[0]`
- `hitToPreviewTarget(hit)` → 尝试生成 Source Preview
- 可预览 → 直接打开 Source Preview
- 不可预览 → 展开来源区域 → `scrollIntoView` → 短暂高亮（1500ms）
- 原有 `[1]`、`【1】` 引用行为保持

---

## 十二、PDF 导出问题根因和解决方法

### 根因

PDF 导出返回 404 不是因为自测题内容，也不是路由代码问题。原因是 8000 端口运行了**旧后端进程**，该进程启动时未加载新加的路由。

### 解决方法

修改后端代码或新增路由后，必须停止旧进程并重启正确项目目录下的后端：

```powershell
# 1. 检查 8000 端口
$Listener = Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue

if ($Listener) {
    $Listener.OwningProcess | Sort-Object -Unique | ForEach-Object {
        Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue
    }
    Start-Sleep -Seconds 1
}

# 2. 使用当前项目目录启动
Set-Location "G:\AI-Workstation\课程资料RAG查询"
& ".\.venv\Scripts\python.exe" -m uvicorn backend.app.main:app --reload --reload-dir backend --reload-dir src --host 127.0.0.1 --port 8000
```

### 确认路由

```http
GET http://127.0.0.1:8000/openapi.json
```

应包含：

```
POST /api/export/document/docx
POST /api/export/document/pdf
POST /api/export/self-test/docx
POST /api/subjects/{subject}/self-test
```

---

## 十三、正确启动命令

### 后端开发（推荐）

```powershell
$Root = "G:\AI-Workstation\课程资料RAG查询"

# 停止旧的 8000 端口进程
$Listener = Get-NetTCPConnection `
    -LocalPort 8000 `
    -State Listen `
    -ErrorAction SilentlyContinue

if ($Listener) {
    $Listener.OwningProcess |
        Sort-Object -Unique |
        ForEach-Object {
            Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue
        }
    Start-Sleep -Seconds 1
}

Set-Location -LiteralPath $Root

& ".\.venv\Scripts\python.exe" `
    -m uvicorn backend.app.main:app `
    --reload `
    --reload-dir backend `
    --reload-dir src `
    --host 127.0.0.1 `
    --port 8000
```

### 后端稳定（展示/答辩）

```powershell
Set-Location "G:\AI-Workstation\课程资料RAG查询"
& ".\.venv\Scripts\python.exe" -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000
```

### 前端

```powershell
Set-Location "G:\AI-Workstation\课程资料RAG查询\frontend"
npm run dev
```

---

## 十四、已完成静态检查

- `backend/app/main.py` — py_compile 通过
- `backend/app/services/self_test_service.py` — py_compile 通过
- `frontend` — `npx tsc --noEmit` 通过
- PDF 路由 — OpenAPI 确认存在，浏览器 PDF 导出实际成功
- E 编号预览 — 代码已写入并通过 TypeScript 检查

---

## 十五、仍需手动测试的场景

1. 单个 PPT 的 single_page 出题
2. 多个 PPT 的 single_page 出题
3. 单个 PPT 跨页 fusion
4. 多个 PPT 跨文件 fusion
5. 页面数少于题目数（均衡复用）
6. 页面数多于题目数（不重复页面）
7. 选择题、填空题、简答题混合
8. inline / end / dual 三种答案模式
9. E1/E2 点击打开 PPT/PDF 页面预览
10. 来源预览（E 编号无法生成预览时展开并高亮）
11. 历史记录中的自测题
12. Word 导出（self-test/docx 和 document/docx）
13. PDF 导出（LibreOffice 需已安装）
14. 无可靠融合关系时降级为 single_page，warning 正确
15. DeepSeek 短暂网络错误后自动重试

---

## 十六、下一步可优化

1. 完整 LLM 回归测试（需要 DeepSeek API key 和建好的资料库）
2. LibreOffice 转换锁（多线程并发预览保护）
3. 融合关系可视化（前端展示 P001 ↔ P003 关系）
4. 更多页面样本时的概念卡提取质量调优
5. 自测题编辑/重新生成（保留上次设置）
