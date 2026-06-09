# 课程资料 RAG 查询项目——下一会话交接文档

> 主题：继续优化自测题生成，重点实现“默认单页出题 / 可选融合出题”。

## 一、项目基本信息

项目路径：

```text
G:\AI-Workstation\课程资料RAG查询
```

主要结构：

- 后端：FastAPI
- 前端：React + Vite + TypeScript
- 向量库：ChromaDB
- 嵌入模型：`bge-small-zh-v1.5`
- AI：DeepSeek / OpenAI-compatible Chat Completions
- 后端入口：`backend/app/main.py`
- 前端主页面：`frontend/src/App.tsx`
- 前端 API：`frontend/src/api.ts`
- 前端类型：`frontend/src/types.ts`
- 普通问答服务：`src/services/qa_service.py`
- 自测题服务：`backend/app/services/self_test_service.py`
- 公共模型请求：`src/llm_deepseek.py`

开始工作前必须先读取并严格遵守项目根目录中的：

```text
AGENTS.md
CLAUDE.md
REASONIX.md（若存在）
```

这些规则文件是硬性边界。

## 二、当前已经完成的功能

项目目前已经具备：

- 课程资料上传、管理、重命名、删除
- PDF / PPT / PPTX 页面预览
- ChromaDB 建库与检索
- 普通智能问答
- 来源依据卡片与页面跳转
- 资料概览
- 复习提纲
- 资料整理 / Longform
- 自测题设置、生成、结果展示
- 历史记录
- Word 导出
- PDF 直接下载
- AI 设置
- 等待动画

## 三、最近完成的关键修改

### 1. 自测题已经独立于普通问答

旧逻辑：

```text
自测题设置
→ 生成提示词
→ 填入普通问答输入框
→ 调用 /qa
→ 按高相关度 Chunk 出题
```

当前逻辑：

```text
点击“开始出题”
→ 前端直接调用 /self-test
→ 后端独立生成自测题
→ 不再经过问题输入框
```

接口：

```text
POST /api/subjects/{subject}/self-test
```

普通智能问答仍使用：

```text
POST /api/subjects/{subject}/qa
```

### 2. 自测题已经改为均衡取材

`self_test_service.py` 当前会：

- 按 `source_filters` 收集当前选择范围内的 Chunk
- 按 `source_path` 分组
- 每个文件内按页码、幻灯片号、Chunk 序号排序
- 从文件开头、中间、结尾均匀抽取
- 多文件之间轮流取样
- 每题约使用 3 个候选 Chunk
- 最少目标 12 个 Chunk
- 最多 36 个 Chunk
- 单个 Chunk 传给模型时最多约 750 字符

自测题已经不再使用普通问答的最高相似度 `top-k` 作为取材方式。

### 3. Self-Test Logic V2 已应用

当前流程：

```text
均衡抽取 Chunk
→ 模型生成 JSON 组卷蓝图
→ 后端校验蓝图
→ 蓝图异常时使用本地降级蓝图
→ 模型正式出题
→ 后端校验正式结果
→ 必要时自动修复一次
→ 返回前端
```

已经加入：

- JSON 蓝图解析
- 题量校验
- 题号校验
- 题型顺序校验
- 真实证据编号校验
- 完全重复题干检测
- 蓝图异常时本地降级
- 正式结果不合格时最多自动修复一次
- 根据题量动态设置 `max_tokens`
- `warning` 返回资料覆盖和修复状态

备份：

```text
backend/app/services/self_test_service.py.bak_logic_v2_20260609_111700
```

### 4. 前端旧自测题流程已清理

已经删除或清理：

- `buildSelfTestPrompt()`
- `isSelfTestRequestRef`
- `setQuestion(prompt)` 旧流程
- 普通 `handleAsk()` 中的自测题分支

当前：

- 普通问答只调用 `/qa`
- “开始出题”直接调用 `/self-test`

### 5. Longform 已提速

已完成：

- 分组摘要 3 路并发
- 根据目标字数动态控制组数和 Chunk 数
- 分阶段等待动画
- 资料整理结果写入历史

### 6. PDF 已改为直接下载

旧逻辑：

```text
点击 PDF
→ window.print()
→ 当前页面进入打印视图
```

当前逻辑：

```text
点击“导出 PDF”
→ POST /api/export/document/pdf
→ 后端生成临时 DOCX
→ LibreOffice 转 PDF
→ 浏览器直接下载 .pdf
```

已覆盖：

- 资料概览
- 复习提纲
- 智能问答
- 自测题
- 资料整理

备份：

```text
backend/app/main.py.bak_pdf_download_20260609_113053
frontend/src/api.ts.bak_pdf_download_20260609_113053
frontend/src/App.tsx.bak_pdf_download_20260609_113053
```

## 四、当前真正需要解决的问题

目前自测题的组卷单位仍然是：

```text
Chunk
```

这会导致：

- 一道题可能只围绕一个 Chunk
- 同一页 PPT 被切成多个 Chunk 后，没有被当作一个整体
- 当前不能由用户选择：
  - 单页出题
  - 融合出题

下一步需要把核心组卷单位升级为：

```text
页面证据单元
```

## 五、已经确认的产品方案

### 默认模式：单页出题

准确含义：

```text
一道题主要依据一页 PPT 或一页 PDF
```

注意：

- 不是“一题一个 Chunk”
- 同一页可能有多个 Chunk，应先合并成一个页面证据单元
- 整套题优先轮换不同页面
- 页面数不足时才允许复用页面

建议页面键：

```python
page_key = (
    source_path,
    slide_number or page_number,
)
```

### 可选模式：融合出题

准确含义：

```text
一道题综合 2～3 个存在明确关系的页面
```

可以是：

- 同一 PPT 的不同页面
- 不同 PPT 的相关页面
- 定义页 + 原理页
- 概念页 + 示例页
- 理论页 + 应用页
- 概念 A + 概念 B 的比较页
- 过程页 + 结果页

不能随机拼接无关页面。

融合关系示例：

```text
定义 + 过程 + 应用
概念 + 示例
原理 + 案例
条件 + 结果
概念 A 与概念 B 的比较
```

最多融合 3 个页面。

## 六、前端需要修改的内容

### 1. 新增类型

在 `frontend/src/types.ts` 增加：

```typescript
export type QuizGenerationMode =
  | "single_page"
  | "fusion";
```

给 `QuizSettings` 增加：

```typescript
generationMode: QuizGenerationMode;
```

默认：

```typescript
generationMode: "single_page"
```

### 2. 自测题设置界面增加选项

在 `frontend/src/App.tsx` 的“自测题生成设置”中增加：

```text
出题方式

● 单页出题（默认）
  每道题主要依据一页 PPT 或 PDF，整套题均衡覆盖不同页面

○ 融合出题
  综合 2～3 个相关页面生成综合题
```

保留现有：

- 题型选择
- 题目数量
- 答案模式
- 结果区
- 来源预览
- 历史记录
- Word / PDF 导出

### 3. 请求体增加参数

```json
{
  "source_filters": [],
  "type_configs": [],
  "answer_mode": "dual",
  "generation_mode": "single_page"
}
```

同步修改：

```text
frontend/src/types.ts
frontend/src/api.ts
frontend/src/App.tsx
```

## 七、后端请求模型需要修改

在 `backend/app/main.py` 的 `SelfTestRequest` 增加：

```python
generation_mode: Literal[
    "single_page",
    "fusion",
] = "single_page"
```

调用 `generate_self_test()` 时传入：

```python
generation_mode=request.generation_mode
```

接口路径不变：

```text
POST /api/subjects/{subject}/self-test
```

## 八、self_test_service.py 的实现方向

主要文件：

```text
backend/app/services/self_test_service.py
```

### 1. 从 Chunk 级改为页面级

建议新增：

```python
_page_key()
_group_chunks_into_pages()
_build_page_units()
_sample_page_units()
```

页面证据单元建议结构：

```python
{
    "page_id": "S1-P8",
    "source_path": "...",
    "file_name": "...",
    "file_type": "pptx",
    "page_number": None,
    "slide_number": 8,
    "chunk_ids": ["..."],
    "text": "该页多个 Chunk 合并后的文本",
    "evidence_ids": ["E3", "E4"]
}
```

### 2. 单页模式规则

当：

```text
generation_mode = "single_page"
```

必须满足：

- 每道题只绑定一个 `page_id`
- 可以引用该页面内多个 `evidence_ids`
- 所有证据必须来自同一 `source_path`
- 页码或幻灯片号必须一致
- 不同题目优先使用尚未出过题的页面
- 页面数不足时才复用

蓝图示例：

```json
{
  "number": 1,
  "type": "choice",
  "difficulty": "easy",
  "mode": "single_page",
  "page_ids": ["S2-P8"],
  "evidence_ids": ["E3", "E4"],
  "knowledge_point": "DFA 的状态转移",
  "objective": "考查对该页核心概念的理解"
}
```

### 3. 融合模式规则

当：

```text
generation_mode = "fusion"
```

建议新增“页面概念和关系提取”阶段：

```text
页面证据单元
→ 提取概念、角色、关键事实
→ 识别页面关系
→ 形成融合页面组
→ 生成蓝图
→ 正式出题
```

页面概念卡示例：

```json
{
  "page_id": "S2-P8",
  "concepts": ["DFA", "状态转移"],
  "role": "mechanism",
  "key_fact": "当前状态和输入符号唯一确定下一状态"
}
```

角色限制为：

```text
definition
condition
mechanism
process
comparison
example
application
conclusion
```

融合组示例：

```json
{
  "group_id": "G3",
  "mode": "fusion",
  "relation": "定义 + 过程 + 应用",
  "page_ids": ["S1-P3", "S1-P6", "S2-P4"],
  "evidence_ids": ["E2", "E7", "E12"]
}
```

程序校验：

- `page_ids` 至少 2 个，最多 3 个
- `relation` 不能为空
- `evidence_ids` 必须真实存在
- `page_ids` 必须与证据实际页面一致
- 页面之间必须有可解释关系
- 没有合适关系时允许降级为单页题，不能硬拼

### 4. 不同题型的融合建议

即使用户选择融合模式，也不应强制所有题融合：

```text
选择题：约 30%～50% 使用融合页面
填空题：以单页为主
简答/大题：优先融合 2～3 个页面
```

### 5. 蓝图 JSON 增加字段

现有字段：

- number
- type
- difficulty
- knowledge_point
- objective
- evidence_ids

新增：

```json
{
  "mode": "single_page 或 fusion",
  "relation": "",
  "page_ids": []
}
```

## 九、已知稳定性问题

曾出现：

```text
SSL: UNEXPECTED_EOF_WHILE_READING
```

位置：

```text
src/llm_deepseek.py
post_chat_completions()
```

当前是否已经加入自动重试，尚未确认。下一会话先检查：

```powershell
Select-String `
  -LiteralPath ".\src\llm_deepseek.py" `
  -Pattern "max_attempts|SSLError|ConnectionError|Timeout|retry_statuses" |
  Select-Object LineNumber, Line
```

如果没有输出，再单独补稳定性修复：

- SSL 错误重试
- ConnectionError 重试
- Timeout 重试
- HTTP 429 / 500 / 502 / 503 / 504 重试
- 最多共尝试 3 次
- API Key 错误、参数错误等永久性 4xx 不重试

前端还需确认：

- 开始新一轮出题前清空旧 `selfTestResult`
- 后端返回 `error_type` 或空 `answer` 时不能把 warning 当新题目
- 失败后不能继续显示上一次题目

## 十、启动方式

### 后端开发模式

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

如果已有 `start_backend.ps1`：

```powershell
.\start_backend.ps1
```

稳定模式：

```powershell
.\start_backend.ps1 -NoReload
```

### 前端

```powershell
cd "G:\AI-Workstation\课程资料RAG查询\frontend"

npm run dev
```

## 十一、修改边界

禁止：

- 运行 Git
- 安装或升级依赖
- 读取 `.env`
- 读取或输出 API Key、token、secret
- 修改正式 ChromaDB
- 重建课程索引
- 删除课程资料
- 做无关重构
- 破坏普通问答
- 破坏历史记录
- 破坏来源预览
- 破坏 Word / PDF 导出

允许：

```powershell
python -m py_compile ...
cd frontend
npx tsc --noEmit
```

## 十二、推荐实施顺序

### 第一步：只读确认

先检查：

- `QuizSettings`
- 自测题设置 UI
- `SelfTestRequest`
- `generateSelfTest()`
- `generate_self_test()`
- 当前蓝图解析与校验代码
- DeepSeek 重试是否存在

不要直接大范围修改。

### 第二步：接入 generation_mode

先打通最小链路：

```text
前端单页/融合选项
→ 请求体 generation_mode
→ 后端模型接收
→ self_test_service 收到参数
```

### 第三步：实现默认单页模式

优先完成：

```text
Chunk → 页面单元
→ 页面级抽样
→ 每题绑定一个页面
→ 页面级蓝图校验
```

### 第四步：实现融合模式

再增加：

```text
页面概念卡
→ 页面关系
→ 融合页面组
→ 融合蓝图校验
```

### 第五步：测试

至少测试：

1. 只选一个 PPT，单页模式
2. 选多个 PPT，单页模式
3. 只选一个 PPT，融合模式
4. 选多个 PPT，融合模式
5. 页面数少于题目数
6. 页面数多于题目数
7. 多题型混合
8. inline / end / dual
9. 来源预览
10. Word 导出
11. PDF 导出
12. 历史记录
13. DeepSeek 网络短暂中断

## 十三、下一会话直接发送的开场消息

```text
请继续处理项目：

G:\AI-Workstation\课程资料RAG查询

先读取并严格遵守项目根目录中的 AGENTS.md、CLAUDE.md、REASONIX.md（若存在）。

我已经完成：
1. 自测题独立 /self-test 接口；
2. 前端点击“开始出题”直接生成，不再经过问题输入框；
3. 自测题均衡抽取 Chunk；
4. JSON 组卷蓝图、程序校验、本地降级蓝图和最多一次自动修复；
5. PDF 改为后端生成并直接下载；
6. Longform 并发提速。

现在要实现：
- 默认 single_page：一道题主要依据一页 PPT/PDF，同一页多个 Chunk 可合并；
- 可选 fusion：一道题融合 2～3 个有关联页面，可以同一 PPT 跨页或跨 PPT；
- 前端增加“单页出题 / 融合出题”，默认 single_page；
- 请求体增加 generation_mode；
- 后端从 Chunk 级组卷改为页面级组卷；
- 单页模式强制每题只绑定一个页面；
- 融合模式先识别页面概念和关系，再组合页面；
- 保留现有题型、答案模式、历史记录、来源预览、Word/PDF 导出；
- 不修改普通问答逻辑。

请先只读检查当前代码，不要直接修改。先给出：
1. 当前相关代码位置；
2. 最小修改文件清单；
3. 页面证据单元的数据结构；
4. 单页模式的校验规则；
5. 融合模式的关系提取与校验方案；
6. 分阶段实施计划。

另外先检查 src/llm_deepseek.py 是否已经具备 SSL/连接/超时自动重试；如果没有，列为稳定性补丁，但不要和页面级组卷混在同一个大补丁中。
```

## 十四、一句话总结

当前自测题已经从“普通相似度问答出题”升级为“独立、均衡取材、JSON 蓝图、程序校验的组卷流程”。

下一步把核心组卷单位从：

```text
Chunk
```

升级为：

```text
页面证据单元
```

并提供：

```text
默认单页出题
+
可选融合出题
```
