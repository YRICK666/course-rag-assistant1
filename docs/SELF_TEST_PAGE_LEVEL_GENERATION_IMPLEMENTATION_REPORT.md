# 自测题页面级组卷实施报告

## 概述

本报告记录自测题从 Chunk 级组卷升级为页面级组卷、引入融合(fusion)出题模式、E 编号来源预览、以及 PDF 导出修复的最终状态。

实施时间：2026-06-09

---

## 一、自测题模式参数

### generation_mode

前后端统一使用 `generation_mode` 控制出题模式：

- `single_page`：单页出题（默认）
- `fusion`：融合出题

**后端** `backend/app/main.py:110`：

```python
class SelfTestRequest(BaseModel):
    source_filters: list[str] = Field(default_factory=list)
    type_configs: list[SelfTestTypeConfig] = Field(default_factory=list)
    answer_mode: Literal["inline", "end", "dual"] = "inline"
    generation_mode: Literal["single_page", "fusion"] = "single_page"
```

**前端** `frontend/src/types.ts:217-229`：

```typescript
export type QuizGenerationMode = "single_page" | "fusion";

export interface QuizSettings {
  typeConfigs: QuizTypeConfig[];
  answerMode: QuizAnswerMode;
  generationMode: QuizGenerationMode;
}
```

前端自测题设置中有"单页出题"和"融合出题"选项。

**请求链路**：

```
QuizSettings.generationMode
→ SelfTestRequest.generation_mode
→ backend SelfTestRequest.generation_mode
→ generate_self_test(generation_mode=...)
```

---

## 二、single_page 页面级组卷

### 核心流程

```
Chunk
→ 按 source_path + slide_number/page_number/chunk_fallback 分组
→ 合并同一页多个 Chunk（去重、截断 2400 字符）
→ 页面均衡抽样（按资料均衡，每文件最多一定数量）
→ 页面内分配 E 编号（轮询分配）
→ LLM 蓝图（页面级约束）
→ 蓝图校验
→ 正式命题
→ 页面级结果校验
```

### 页码规则

- PPT/PPTX 使用 `slide_number`
- PDF 使用 `page_number`
- 缺失页码的 Chunk 独立形成 `chunk_fallback` 页面单元
- 通过 `_page_location()` 判断（`self_test_service.py:283-315`）

### 页面合并 `_merge_page_text()`

- 同页多个 Chunk 按 `chunk_sort_key` 排序
- 逐 Chunk 合并，去重（归一化比较）
- 每页上限 2400 字符
- 位置：`self_test_service.py:336-372`

### 页面证据分配 `_assign_page_evidence()`

- 所有页面轮询分配 Chunk 作为证据
- 每个 Chunk 生成一个 E 编号（E1、E2……）
- 记录 `evidence_to_page` 映射
- 位置：`self_test_service.py:585-703`

### 蓝图约束

1. 一道题只能绑定一个页面
2. 可以引用同一页多个 E 编号
3. 页面足够时优先不重复页面
4. 页面不足时均衡复用
5. 本地 fallback 蓝图也按页面轮换

### 校验规则 `_validate_blueprint()`

- 题量、题型、顺序必须完全匹配
- `single_page` 题只能有一个 `page_id`
- 证据不得跨越页面
- 可用页面充足时不得重复使用页面
- 位置：`self_test_service.py:1683-1997`

### 结果校验 `_validate_final_answer()`

- 逐题检查〖依据〗中的 E 编号是否属于该题指定页面
- `single_page` 题的正式依据必须属于其唯一页面
- 不允许引用未分配给该题的证据编号
- 位置：`self_test_service.py:2226-2433`

---

## 三、fusion 页面融合组卷

### 阶段一：页面概念卡提取

调用 LLM 为每个页面提取概念卡（`_extract_page_concept_cards()`，`self_test_service.py:1228-1285`）。

概念卡结构：

```json
{
  "page_id": "P001",
  "concepts": ["核心概念"],
  "role": "definition",
  "key_facts": ["该页最重要的事实"],
  "prerequisites": [],
  "outcomes": []
}
```

### 角色枚举

```python
PAGE_CONCEPT_ROLES = {
    "definition", "condition", "mechanism",
    "process", "comparison", "example",
    "application", "conclusion",
}
```

位置：`self_test_service.py:72-81`

### 校验规则

- 每个概念卡必须有唯一 `page_id`
- `page_id` 必须在有效页面集合中
- `role` 必须属于预定义角色
- 必须包含 `concepts` 和 `key_facts`
- 所有有效页面必须有概念卡

### 阶段二：角色与关系识别

通过 `_relation_for_cards()` 计算页面间关系（`self_test_service.py:1336-1404`）：

- 共享概念（`shared_concepts`）：两个页面出现相同概念名
- 前向链接（`outcomes` → `prerequisites`）：一页的产出是另一页的前提
- 角色对自动映射关系类型

### 角色→关系映射

```python
ROLE_RELATION_TYPES = {
    frozenset({"definition", "example"}): "definition_example",
    frozenset({"definition", "application"}): "definition_application",
    frozenset({"condition", "conclusion"}): "condition_result",
    frozenset({"condition", "mechanism"}): "cause_effect",
    frozenset({"mechanism", "application"}): "mechanism_application",
    frozenset({"mechanism", "example"}): "theory_case",
    frozenset({"comparison"}): "comparison",
}
```

`process` + `process` 自动映射为 `process_sequence`。

位置：`self_test_service.py:94-102`

### 关系类型

```python
FUSION_RELATION_TYPES = {
    "definition_example", "definition_application",
    "condition_result", "process_sequence",
    "mechanism_application", "theory_case",
    "comparison", "cause_effect",
}
```

位置：`self_test_service.py:83-92`

### 阶段三：融合组构建

`_build_fusion_groups()`（`self_test_service.py:1423-1627`）：

1. 遍历所有有效页面对进行关系打分
2. 二维组：共享概念 ×5 + 链接概念 ×6 + 同文件 +1 + 相邻距离 ≤3 +1
3. 按分数降序排列，去重
4. 三维组：从得分最高的二维组扩展，至少与两个页面都有关系
5. 最多 `MAX_FUSION_GROUPS = 12` 组
6. 同一 PPT 跨页或跨 PPT 均可

### 阶段四：融合决策

`_should_use_fusion()`（`self_test_service.py:1657-1680`）：

| 题型 | 规则 |
|---|---|
| 填空题(fill) | 不融合，始终 single_page |
| 简答/大题(essay) | 优先融合 |
| 选择题(choice) | 约一半融合（基于 `choice_quota`） |

### 阶段五：降级规则

- 概念卡提取失败 → 降级为 single_page
- 无通过校验的融合组 → 降级为 single_page
- fallback 页面不参与融合
- 降级时 `warning` 会说明情况

### 阶段六：结果校验（fusion 特有）

- fusion 题必须绑定 2～3 个页面
- 必须匹配到经过程序校验的关系组
- `relation_type` 必须与候选关系组一致
- 证据必须覆盖全部融合页面
- 不允许引用未分配给该题的证据

---

## 四、证据编号（E 编号）

### 编号规则

- 从 Chunk 分配为证据时生成 `E1`、`E2`……
- `rank` 从 1 递增，与 `hits` 数组的 0-based index 对应：`E1 → hits[0]`
- 映射存储在 `evidence_to_page`：`{"E1": "P001", "E2": "P001", ...}`

### 前端预览

`renderInlineAssistant()` 中正则（`App.tsx:824`）：

```typescript
const pattern = /(\*\*[^*]+\*\*)|\[(\d+)\]|【(\d+)】|(?:\[|【)?\bE(\d+)\b(?:\]|】)?/gi;
```

支持识别：
- `E1`
- `E2`
- `[E1]`
- `【E1】`

点击 E 编号后，`handleSelfTestSourceClick()` 映射：
1. `E1 → targetIndex = 0 → hits[0]` → `hitToPreviewTarget()`
2. 如果可生成页面预览 → 直接打开 Source Preview
3. 如果不能 → 展开来源依据区域 → 滚动到对应来源卡片 → 高亮

---

## 五、Word/PDF 导出

### DOCX 导出

- `POST /api/export/self-test/docx`（自测题专用）
- `POST /api/export/document/docx`（通用文档）
- 通过 `python-docx` 生成 DOCX，Content-Disposition 触发浏览器下载

### PDF 导出

- `POST /api/export/document/pdf`
- 流程：生成临时 DOCX → LibreOffice headless → PDF → 浏览器下载
- 需要已安装 LibreOffice（soffice 可调用）
- LibreOffice 版本已确认：26.2.3.2 工作正常

### PDF 导出 404 问题根因

**问题**：此前 PDF 路由返回 404。

**根因**：8000 端口运行了未加载新路由的旧后端进程。修改后端代码或新增路由后，必须重启**正确项目目录下的后端**，不能继续使用旧进程。

**确认方法**：`http://127.0.0.1:8000/openapi.json`。

确认可用的路由：

```
POST /api/export/document/docx
POST /api/export/document/pdf
POST /api/export/self-test/docx
```

---

## 六、DeepSeek 重试

`src/llm_deepseek.py` 中 `post_chat_completions()`（`llm_deepseek.py:76-162`）当前重试机制：

| 特性 | 值 |
|---|---|
| 最大尝试次数 | `max_attempts = 3` |
| 重试前等待 | `min(2.0, 0.7 * attempt)` 秒 |
| HTTP 重试状态 | 429, 500, 502, 503, 504 |
| SSL 错误重试 | 是 |
| ConnectionError 重试 | 是 |
| Timeout 重试 | 是 |
| 非可重试 HTTP 错误 | 立即抛出 DeepSeekError |
| 其他 RequestException | 立即抛出 DeepSeekError |

---

## 七、启动方式

### 后端开发

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

### 前端

```powershell
Set-Location "G:\AI-Workstation\课程资料RAG查询\frontend"
npm run dev
```

### 稳定模式

```powershell
Set-Location "G:\AI-Workstation\课程资料RAG查询"
& ".\.venv\Scripts\python.exe" -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000
```

### 确认路由

访问 `http://127.0.0.1:8000/openapi.json` 查看实际加载的路由。

---

## 八、验证记录

### 已完成验证

- `backend/app/main.py` — Python 语法通过 (py_compile)
- `backend/app/services/self_test_service.py` — Python 语法通过 (py_compile)
- `frontend` — `npx tsc --noEmit` 通过
- PDF 导出路由 — 重新加载后 OpenAPI 已显示，浏览器 PDF 导出实际成功
- E 编号预览代码 — 已写入并通过 TypeScript 类型检查
- `src/llm_deepseek.py` — 重试逻辑确认（只读，未修改）

### 尚未执行

- 真实 LLM 回归测试（需要 DeepSeek API key 和完整资料库）

---

## 九、手动测试清单

1. 单个 PPT 的 single_page 出题
2. 多个 PPT 的 single_page 出题
3. 单个 PPT 跨页 fusion
4. 多个 PPT 跨文件 fusion
5. 页面数少于题目数
6. 页面数多于题目数
7. 选择题、填空题、简答题混合
8. inline / end / dual 三种答案模式
9. E1/E2 点击打开 PPT/PDF 页面预览
10. 来源预览（E 编号无法生成预览时展开来源卡片并高亮）
11. 历史记录中的自测题
12. Word 导出（self-test/docx 和 document/docx）
13. PDF 导出（LibreOffice 需已安装）
14. 无可靠融合关系时降级为 single_page，warning 提示正确
15. DeepSeek 短暂网络错误后自动重试
