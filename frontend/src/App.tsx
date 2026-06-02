import { ChangeEvent, FormEvent, useEffect, useMemo, useRef, useState } from "react";
import type { CSSProperties, PointerEvent as ReactPointerEvent } from "react";
import {
  askQuestion,
  API_BASE_URL,
  buildIndex,
  convertPptMaterial,
  createSubject,
  deleteMaterial,
  deleteQaHistoryItem,
  exportDocumentDocx,
  exportSelfTestDocx,
  fetchAiSettings,
  fetchLongformAnalysis,
  fetchQaHistory,
  fetchSnippetKeywords,
  fetchOverview,
  fetchStudyGuide,
  fetchSubjectMaterials,
  fetchSubjects,
  fetchSubjectStatus,
  renameMaterial,
  saveAiSettings,
  uploadMaterials
} from "./api";
import {
  materials as mockMaterials,
  subjects as mockSubjects
} from "./mockData";
import type {
  BuildIndexRequest,
  AiProvider,
  AiSettings,
  ChatMessage,
  ExportDocumentRequest,
  Hit,
  LongformGroupSummary,
  LongformResponse,
  LongformType,
  Material,
  MaterialStatus,
  OverviewSource,
  QAHistoryItem,
  QuizAnswerMode,
  QuizQuestionType,
  QuizSettings,
  QuizTypeConfig,
  SelfTestResult,
  SourceReference,
  StudyGuideSource,
  Subject,
  SubjectStatus
} from "./types";

const statusClass: Record<MaterialStatus, string> = {
  已建库: "status-ready",
  未建库: "status-missing",
  待转换: "status-pending",
  索引异常: "status-missing"
};

const quickQuestionPool = [
  "这一部分主要讲了什么？",
  "这章的主线是什么？",
  "我应该先看哪几页？",
  "帮我梳理重点和难点",
  "这部分最容易混淆什么？",
  "给我一份复习思路",
  "用简单例子解释这个概念",
  "这几个概念之间有什么关系？",
  "哪些地方可能会考？",
  "哪几页最值得重点看？",
  "这部分有没有对应的 PPT 依据？",
  "帮我找出最关键的来源页"
];

const typeLabels: Record<string, string> = {
  choice: "选择题",
  fill: "填空题",
  essay: "简答/大题"
};

const answerModeOptions: { value: QuizAnswerMode; label: string; desc: string }[] = [
  { value: "inline", label: "每题后显示答案解析", desc: "题目1 → 答案解析 → 题目2 → 答案解析" },
  { value: "end", label: "题目集中在前，答案解析统一放卷尾", desc: "所有题目先出现，最后统一给出答案与解析" },
  { value: "dual", label: "生成练习版 + 解析版", desc: "练习版无答案，解析版含答案、解析、考点" },
];

function buildSelfTestPrompt(settings: QuizSettings): string {
  const enabledTypes = settings.typeConfigs.filter((tc) => tc.enabled);
  const totalCount = enabledTypes.reduce((sum, tc) => sum + tc.count, 0);

  let prompt = `请基于当前课程资料生成一组期末复习自测题。\n\n`;
  prompt += `要求：\n\n1. 只根据已检索到的课程资料出题，不要编造资料外知识。\n\n`;
  prompt += `2. 共生成 ${totalCount} 道题，按以下顺序和数量：\n`;
  enabledTypes.forEach((tc) => {
    prompt += `   * ${tc.count} 道${typeLabels[tc.type]}\n`;
  });

  prompt += `\n3. `;
  if (settings.answerMode === "inline") {
    prompt += `输出时使用固定大标题 \`## 解析版\`，每道题后立即给出【答案】【解析】【考点】。`;
  } else if (settings.answerMode === "end") {
    prompt += `输出时使用固定大标题 \`## 解析版\`。先完整输出所有题目，不要夹杂答案；\n   最后单独输出 \`## 答案与解析\` 区。`;
  } else {
    prompt += `输出固定分隔标题：\n   \`## 练习版\`\n   \`## 解析版\`\n   其中：\n   * 练习版只包含题目，不包含答案解析\n   * 解析版包含题目、答案、解析、考点`;
  }

  prompt += `\n\n4. 禁止生成不在上述列表中的题型。\n\n`;
  prompt += `5. 难度以课堂复习和期末考试为准，不要过度拔高。\n\n`;
  prompt += `6. 输出要清晰，不要使用 Markdown 表格。\n\n`;
  prompt += `7. 请严格按照用户指定的题型顺序组织输出。`;
  return prompt;
}

interface PrintPayload {
  title: string;
  subject: string;
  scopeLabel: string;
  generatedAt: string;
  content: string;
  sources: Hit[];
  includeSources: boolean;
  filename: string;
}

/** Extract citation numbers [N] or 【N】 from content, sorted unique. */
function extractCitationNumbers(content: string): number[] {
  const nums = new Set<number>();
  const re = /(?:\[|【)(\d+)(?:\]|】)/g;
  let match: RegExpExecArray | null;
  while ((match = re.exec(content)) !== null) {
    nums.add(Number.parseInt(match[1], 10));
  }
  return Array.from(nums).sort((a, b) => a - b);
}

/** Strip [N] / 【N】 citation marks from content. */
function stripCitationMarks(content: string): string {
  return content.replace(/(?:\[|【)\d+(?:\]|】)/g, "").trim();
}

/** Build formatted source list text from sources, optionally filtered by citation numbers. */
function buildSourcesText(sources: Hit[], citationNumbers?: number[]): string {
  const filtered = citationNumbers && citationNumbers.length > 0
    ? citationNumbers
        .map((n) => ({ index: n, source: sources[n - 1] }))
        .filter((item) => item.source)
    : sources.slice(0, 10).map((source, i) => ({ index: i + 1, source }));
  const label = citationNumbers && citationNumbers.length > 0 ? "参考来源" : "主要来源";
  const lines = filtered.map(({ index, source }) => {
    const meta = (source.metadata || {}) as Record<string, unknown>;
    const fileName = String(meta.file_name || meta.source_path || "未知来源");
    const page = meta.page_number;
    const slide = meta.slide_number;
    const location = page ? `第 ${page} 页` : slide ? `第 ${slide} 张幻灯片` : "位置未知";
    return `[${index}] ${fileName}，${location}`;
  });
  return `\n${lines.join("\n")}`;
}

const defaultAiSettings: AiSettings = {
  enabled: true,
  provider: "deepseek",
  base_url: "https://api.deepseek.com",
  model: "deepseek-v4-flash",
  has_api_key: false,
  profiles: {
    deepseek: {
      base_url: "https://api.deepseek.com",
      model: "deepseek-v4-flash",
      has_api_key: false
    },
    openai_compatible: {
      base_url: "",
      model: "",
      has_api_key: false
    }
  }
};

const deepseekModelOptions = [
  {
    value: "deepseek-v4-flash",
    title: "快速模式",
    modelName: "deepseek-v4-flash",
    description: "快速 / 日常学习 / 默认推荐"
  },
  {
    value: "deepseek-v4-pro",
    title: "深度模式",
    modelName: "deepseek-v4-pro",
    description: "深度 / 复杂推理"
  }
];

function getAiModelModeLabel(model: string): string {
  if (model === "deepseek-v4-flash") {
    return "快速模式";
  }
  if (model === "deepseek-v4-pro") {
    return "深度模式";
  }
  return "自定义模型";
}

function getAiProviderProfile(settings: AiSettings, provider: AiProvider) {
  return settings.profiles?.[provider] ?? {
    base_url: provider === "deepseek" ? "https://api.deepseek.com" : "",
    model: provider === "deepseek" ? "deepseek-v4-flash" : "",
    has_api_key: false
  };
}

function pickQuickQuestions(): string[] {
  return [...quickQuestionPool]
    .sort(() => Math.random() - 0.5)
    .slice(0, 3);
}
const allowedUploadExtensions = [".ppt", ".pptx", ".pdf", ".docx", ".txt"];
const SIDEBAR_WIDTH_STORAGE_KEY = "course-rag-sidebar-width";
const SIDEBAR_COLLAPSED_STORAGE_KEY = "course-rag-sidebar-collapsed";
const INSIGHT_HEIGHT_STORAGE_KEY = "course-rag-insight-height";
const LAST_SUBJECT_STORAGE_KEY = "courseRag:lastSubject";
const LAST_SOURCE_FILTERS_KEY = "courseRag:lastSourceFilters";
const DEFAULT_SUBJECT = "形式语言与自动机";
const SIDEBAR_WIDTH_DEFAULT = 280;
const SIDEBAR_WIDTH_MIN = 240;
const SIDEBAR_WIDTH_MAX = 420;
const INSIGHT_HEIGHT_DEFAULT = 320;
const INSIGHT_HEIGHT_MIN = 120;
const INSIGHT_HEIGHT_MAX = 460;
const RESIZE_BREAKPOINT = 1120;
const EVIDENCE_SUMMARY_LENGTH = 160;

function clampNumber(value: number, min: number, max: number): number {
  return Math.min(Math.max(value, min), max);
}

function readStoredDimension(key: string, fallback: number, min: number, max: number): number {
  if (typeof window === "undefined") {
    return fallback;
  }
  try {
    const rawValue = window.localStorage.getItem(key);
    if (rawValue === null) {
      return fallback;
    }
    const storedValue = Number(rawValue);
    if (!Number.isFinite(storedValue)) {
      return fallback;
    }
    return clampNumber(storedValue, min, max);
  } catch {
    return fallback;
  }
}

function saveStoredDimension(key: string, value: number): void {
  if (typeof window === "undefined") {
    return;
  }
  try {
    window.localStorage.setItem(key, String(Math.round(value)));
  } catch {
    // Ignore storage failures; dragging should still work for the current session.
  }
}

function readStoredBoolean(key: string, fallback: boolean): boolean {
  if (typeof window === "undefined") {
    return fallback;
  }
  try {
    const rawValue = window.localStorage.getItem(key);
    if (rawValue === null) {
      return fallback;
    }
    return rawValue === "true";
  } catch {
    return fallback;
  }
}

function saveStoredBoolean(key: string, value: boolean): void {
  if (typeof window === "undefined") {
    return;
  }
  try {
    window.localStorage.setItem(key, String(value));
  } catch {
    // Ignore storage failures; the current session can still keep the state.
  }
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

function shortenText(text: string, maxLength = 220): string {
  const compact = text.replace(/\s+/g, " ").trim();
  if (compact.length <= maxLength) {
    return compact;
  }
  return `${compact.slice(0, maxLength)}...`;
}

function metadataText(metadata: Record<string, unknown>, key: string): string {
  const value = metadata[key];
  return typeof value === "string" || typeof value === "number" ? String(value) : "";
}

function hitToSourceReference(hit: Hit, index: number): SourceReference {
  const fileName = metadataText(hit.metadata, "file_name") || metadataText(hit.metadata, "source_path") || "未知来源";
  const page = metadataText(hit.metadata, "page_number");
  const slide = metadataText(hit.metadata, "slide_number");
  const paragraph = metadataText(hit.metadata, "paragraph_number");
  const chunk = metadataText(hit.metadata, "chunk_id") || metadataText(hit.metadata, "chunk_index");
  const location = page
    ? `第 ${page} 页`
    : slide
      ? `第 ${slide} 张幻灯片`
      : paragraph
        ? `第 ${paragraph} 段`
        : chunk
          ? `chunk ${chunk}`
          : hit.source || "位置未知";

  return {
    id: `${hit.rank ?? index}-${fileName}-${location}`,
    fileName,
    location,
    similarity: hit.similarity ?? hit.hybrid_score ?? 0,
    summary: shortenText(hit.text || "暂无片段摘要。")
  };
}

function shouldShowBuildActions(errorType?: string, warning?: string | null): boolean {
  if (errorType === "index_corrupted" || errorType === "index_empty") {
    return true;
  }
  const text = warning || "";
  return (
    text.includes("知识库索引可能已损坏") ||
    text.includes("还没有可用知识库") ||
    text.includes("请先建立知识库")
  );
}

function hasReadyIndex(status: SubjectStatus): boolean {
  return status.indexed_count > 0 && status.index_status !== "corrupted";
}

function toPreviewNumber(value: unknown): number | null {
  if (typeof value === "number" && Number.isFinite(value)) {
    return value;
  }
  if (typeof value === "string" && value.trim()) {
    const numberValue = Number(value);
    return Number.isFinite(numberValue) ? numberValue : null;
  }
  return null;
}

function metadataNumber(metadata: Record<string, unknown>, key: string): number | null {
  return toPreviewNumber(metadata[key]);
}

type PreviewFileType = "pdf" | "ppt" | "pptx";

type PreviewReferenceTarget = {
  sourcePath: string;
  fileName: string;
  fileType: PreviewFileType;
  pageNumber: number;
  snippet?: string;
  title?: string;
};

function normalizePreviewFileType(value: string): PreviewFileType | null {
  const normalized = value.trim().toLowerCase().replace(/^\./, "");
  if (normalized === "pdf" || normalized === "ppt" || normalized === "pptx") {
    return normalized;
  }
  if (normalized.endsWith(".pdf")) {
    return "pdf";
  }
  if (normalized.endsWith(".pptx")) {
    return "pptx";
  }
  if (normalized.endsWith(".ppt")) {
    return "ppt";
  }
  return null;
}

function getHitFileType(hit: Hit): PreviewFileType | null {
  const metadata = hit.metadata ?? {};
  return (
    normalizePreviewFileType(metadataText(metadata, "file_type")) ||
    normalizePreviewFileType(metadataText(metadata, "file_name")) ||
    normalizePreviewFileType(metadataText(metadata, "source_path"))
  );
}

function getHitPreviewPageNumber(hit: Hit): number | null {
  const fileType = getHitFileType(hit);
  if (fileType === "pdf") {
    return metadataNumber(hit.metadata, "page_number");
  }
  if (fileType === "ppt" || fileType === "pptx") {
    return metadataNumber(hit.metadata, "slide_number");
  }
  return null;
}

function canPreviewHit(hit: Hit): boolean {
  const sourcePath = metadataText(hit.metadata, "source_path");
  const fileType = getHitFileType(hit);
  const pageNumber = getHitPreviewPageNumber(hit);
  return Boolean(sourcePath && fileType && pageNumber !== null && pageNumber >= 1);
}

function buildPageImageUrl(subject: string, hitOrSourcePath: Hit | string, pageNumber?: number | null): string {
  if (!subject) {
    return "";
  }

  if (typeof hitOrSourcePath === "string") {
    if (!hitOrSourcePath || pageNumber === undefined || pageNumber === null || pageNumber < 1) {
      return "";
    }
    return `${API_BASE_URL}/api/subjects/${encodeURIComponent(subject)}/materials/page-image?source_path=${encodeURIComponent(hitOrSourcePath)}&page_number=${Math.trunc(pageNumber)}`;
  }

  if (!canPreviewHit(hitOrSourcePath)) {
    return "";
  }
  const sourcePath = metadataText(hitOrSourcePath.metadata, "source_path");
  const previewPageNumber = getHitPreviewPageNumber(hitOrSourcePath);
  if (previewPageNumber === null) {
    return "";
  }
  return buildPageImageUrl(subject, sourcePath, previewPageNumber);
}

function getReferenceFileType(reference: StudyGuideSource): PreviewFileType | null {
  return (
    normalizePreviewFileType(reference.file_type || "") ||
    normalizePreviewFileType(reference.file_name || "") ||
    normalizePreviewFileType(reference.source_path || "")
  );
}

function getReferencePreviewPageNumber(reference: StudyGuideSource, fileType: PreviewFileType): number | null {
  if (fileType === "pdf") {
    return toPreviewNumber(reference.page_number);
  }
  return toPreviewNumber(reference.slide_number);
}

function previewLocationText(fileType: PreviewFileType, pageNumber: number): string {
  return fileType === "pdf"
    ? `第 ${Math.trunc(pageNumber)} 页`
    : `第 ${Math.trunc(pageNumber)} 张幻灯片`;
}

function cleanReferenceSnippet(text: string | undefined): string {
  return (text || "")
    .replace(/\b(?:page|p\.?)\s*\d+\b/gi, " ")
    .replace(/第\s*\d+\s*(?:页|頁|张幻灯片|張幻燈片|张|張)/g, " ")
    .replace(/幻灯片\s*\d+/g, " ")
    .replace(/\bchunk\s*\d+\b/gi, " ")
    .replace(/[-=_*#~·•]{4,}/g, " ")
    .replace(/([，。！？；：,.!?;:、])\1{2,}/g, "$1$1")
    .replace(/\s+/g, " ")
    .trim();
}

function getReferenceHint(prefix: string, text?: string): string {
  const cleaned = cleanReferenceSnippet(text);
  const maxLength = 82;
  const hint = cleaned
    ? cleaned.length > maxLength
      ? `${cleaned.slice(0, maxLength)}...`
      : cleaned
    : "点击查看该页原始内容";

  return `${prefix}${hint}`;
}

function cleanPreviewSnippet(text: string | undefined): string {
  let cleaned = text || "";
  let previous = "";

  while (cleaned !== previous) {
    previous = cleaned;
    cleaned = cleaned.replace(
      /^\s*(?:\d{4}[\/.-]\d{1,2}[\/.-]\d{1,2}\s+\d+|第\s*\d+\s*(?:页|頁|张幻灯片|張幻燈片|张|張)|幻灯片\s*\d+|\b(?:slide|page|chunk)\s*\d+\b)\s*(?:[，。；：,.?？!！、-]\s*)?/i,
      " "
    );
  }

  return cleaned
    .replace(/[-=_*#~·•]{4,}/g, " ")
    .replace(/([，。！？；：,.!?;:、])\1{2,}/g, "$1$1")
    .replace(/\s+/g, " ")
    .trim();
}

const HIGHLIGHT_STOP_WORDS = new Set([
  "这个",
  "这些",
  "主要",
  "通过",
  "了解",
  "掌握",
  "课程",
  "能力",
  "知识",
  "基本",
  "进行",
  "以及",
  "或者",
  "一个",
  "我们",
  "学生",
  "学习",
  "可以",
  "需要",
  "内容",
  "相关",
  "部分"
]);

const COURSE_HIGHLIGHT_TERMS = [
  "非确定有穷自动机",
  "确定有穷自动机",
  "上下文无关语言",
  "上下文有关语言",
  "形式化描述",
  "递归定义",
  "语言运算",
  "状态转换",
  "识别模型",
  "有穷自动机",
  "正则语言",
  "形式语言",
  "图灵机",
  "产生式",
  "抽象思维",
  "自动机",
  "字母表",
  "字符串",
  "DFA",
  "NFA",
  "文法",
  "推导",
  "空串",
  "闭包",
  "递归"
].sort((left, right) => right.length - left.length);

function addHighlightTerm(terms: string[], term: string) {
  const normalized = term.replace(/\s+/g, " ").trim();
  if (
    normalized.length < 2 ||
    HIGHLIGHT_STOP_WORDS.has(normalized) ||
    terms.some((existing) => existing.toLowerCase() === normalized.toLowerCase())
  ) {
    return;
  }
  terms.push(normalized);
}

function extractHighlightTerms(text: string): string[] {
  const terms: string[] = [];

  for (const term of COURSE_HIGHLIGHT_TERMS) {
    if (text.toLowerCase().includes(term.toLowerCase())) {
      addHighlightTerm(terms, term);
      if (terms.length >= 5) {
        return terms;
      }
    }
  }

  const bracketPattern = /[《「『“"（(【\[]([^》」』”"）)】\]]{2,30})[》」』”"）)】\]]/g;
  for (const match of text.matchAll(bracketPattern)) {
    addHighlightTerm(terms, match[1]);
    if (terms.length >= 5) {
      return terms;
    }
  }

  const englishPattern = /\b[A-Z]{2,}(?:\/[A-Z]{2,})?\b|\b[A-Za-z][A-Za-z0-9-]*(?:\s+[A-Za-z][A-Za-z0-9-]*){1,2}\b/g;
  for (const match of text.matchAll(englishPattern)) {
    addHighlightTerm(terms, match[0]);
    if (terms.length >= 5) {
      return terms;
    }
  }

  const chinesePattern = /[\u4e00-\u9fff]{2,8}/g;
  for (const match of text.matchAll(chinesePattern)) {
    addHighlightTerm(terms, match[0]);
    if (terms.length >= 5) {
      return terms;
    }
  }

  return terms;
}

function normalizeSnippetKeywords(text: string, keywords: string[]): string[] {
  const terms: string[] = [];
  const source = text.toLowerCase();
  for (const keyword of keywords) {
    const normalized = keyword.replace(/\s+/g, " ").trim();
    if (!normalized || !source.includes(normalized.toLowerCase())) {
      continue;
    }
    addHighlightTerm(terms, normalized);
    if (terms.length >= 5) {
      break;
    }
  }
  return terms;
}

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function renderHighlightedSnippet(text: string, terms: string[]) {
  if (!terms.length) {
    return text;
  }

  const pattern = new RegExp(`(${terms.map(escapeRegExp).join("|")})`, "gi");
  return text.split(pattern).map((part, index) => {
    const matched = terms.some((term) => term.toLowerCase() === part.toLowerCase());
    return matched ? (
      <mark className="review-highlight" key={`${part}-${index}`}>
        {part}
      </mark>
    ) : (
      part
    );
  });
}

function sourceToPreviewTarget(source: StudyGuideSource): PreviewReferenceTarget | null {
  const sourcePath = source.source_path || "";
  const fileType = getReferenceFileType(source);
  if (!sourcePath || !fileType) {
    return null;
  }

  const pageNumber = getReferencePreviewPageNumber(source, fileType);
  if (pageNumber === null || pageNumber < 1) {
    return null;
  }

  return {
    sourcePath,
    fileName: source.file_name || source.label || sourcePath || "未知来源",
    fileType,
    pageNumber,
    snippet: source.text || ""
  };
}

function hitToPreviewTarget(hit: Hit): PreviewReferenceTarget | null {
  const metadata = hit.metadata ?? {};
  const sourcePath = metadataText(metadata, "source_path");
  const fileType = getHitFileType(hit);
  const pageNumber = getHitPreviewPageNumber(hit);
  if (!sourcePath || !fileType || pageNumber === null || pageNumber < 1) {
    return null;
  }
  return {
    sourcePath,
    fileName: metadataText(metadata, "file_name") || sourcePath,
    fileType,
    pageNumber,
    snippet: hit.text || "",
  };
}

function samePreviewTarget(left: PreviewReferenceTarget, right: PreviewReferenceTarget): boolean {
  return (
    left.sourcePath === right.sourcePath &&
    left.fileType === right.fileType &&
    Math.trunc(left.pageNumber) === Math.trunc(right.pageNumber) &&
    left.snippet === right.snippet
  );
}

function buildSourcePreviewTargets(sources: StudyGuideSource[]): PreviewReferenceTarget[] {
  return sources.flatMap((source) => {
    const target = sourceToPreviewTarget(source);
    return target ? [target] : [];
  });
}

function buildReferencePreviews(subject: string, references: StudyGuideSource[]) {
  const previews: Array<PreviewReferenceTarget & {
    key: string;
    imageUrl: string;
  }> = [];

  for (const [index, reference] of references.entries()) {
    const sourcePath = reference.source_path || "";
    const fileType = getReferenceFileType(reference);
    if (!fileType) {
      continue;
    }

    const pageNumber = getReferencePreviewPageNumber(reference, fileType);
    const imageUrl = buildPageImageUrl(subject, sourcePath, pageNumber);
    if (!imageUrl || pageNumber === null) {
      continue;
    }

    previews.push({
      key: `${sourcePath}-${fileType}-${pageNumber}-${index}`,
      imageUrl,
      sourcePath,
      fileName: reference.file_name || reference.label || sourcePath || "未知来源",
      fileType,
      pageNumber,
      snippet: reference.text || ""
    });

    if (previews.length >= 2) {
      break;
    }
  }

  return previews;
}

function PagePreviewThumb({ imageUrl, alt }: { imageUrl: string; alt: string }) {
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    setFailed(false);
  }, [imageUrl]);

  if (failed) {
    return (
      <div className="related-page-thumb related-page-thumb-error" role="img" aria-label={alt}>
        预览生成失败
      </div>
    );
  }

  return (
    <img
      className="related-page-thumb"
      src={imageUrl}
      alt={alt}
      onError={() => setFailed(true)}
    />
  );
}

function getEvidenceLevel(index: number) {
  if (index <= 1) {
    return { text: "核心依据", className: "evidence-level-core" };
  }
  if (index <= 4) {
    return { text: "重要补充", className: "evidence-level-important" };
  }
  return { text: "参考片段", className: "evidence-level-reference" };
}

function renderMarkdown(content: string) {
  return content
    .split(/\r?\n/)
    .map((rawLine, index) => {
      const line = rawLine.trim();
      if (!line) {
        return null;
      }
      if (line.startsWith("### ")) {
        return <h3 key={`heading-${index}`}>{line.slice(4)}</h3>;
      }
      if (line.startsWith("## ")) {
        return <h3 key={`heading-${index}`}>{line.slice(3)}</h3>;
      }
      if (line.startsWith("# ")) {
        return <h3 key={`heading-${index}`}>{line.slice(2)}</h3>;
      }
      if (line.startsWith("- ")) {
        return <li key={`item-${index}`}>{line.slice(2)}</li>;
      }
      return <p key={`paragraph-${index}`}>{line}</p>;
    })
    .filter(Boolean);
}

function renderAssistantContent(
  content: string,
  maxSourceNum: number,
  onSourceClick: (num: number) => void
) {
  return content
    .split(/\r?\n/)
    .map((rawLine, lineIndex) => {
      const line = rawLine.trim();
      if (!line) {
        return null;
      }
      return (
        <p key={`qa-line-${lineIndex}`} className="assistant-paragraph">
          {renderInlineAssistant(line, maxSourceNum, onSourceClick)}
        </p>
      );
    })
    .filter(Boolean);
}

function renderInlineAssistant(
  text: string,
  maxSourceNum: number,
  onSourceClick: (num: number) => void
) {
  const segments: Array<string | JSX.Element> = [];
  const pattern = /(\*\*[^*]+\*\*)|\[(\d+)\]|【(\d+)】/g;
  let lastIndex = 0;
  let match: RegExpExecArray | null;

  while ((match = pattern.exec(text)) !== null) {
    if (match.index > lastIndex) {
      segments.push(text.slice(lastIndex, match.index));
    }

    if (match[1]) {
      // Bold **text**
      segments.push(<strong key={`s${segments.length}`}>{match[1].slice(2, -2)}</strong>);
    } else {
      const num = parseInt(match[2] ?? match[3], 10);
      if (num >= 1 && num <= maxSourceNum) {
        segments.push(
          <button
            key={`s${segments.length}`}
            className="assistant-source-link"
            type="button"
            onClick={() => onSourceClick(num)}
          >
            [{num}]
          </button>
        );
      } else {
        segments.push(match[0]);
      }
    }

    lastIndex = match.index + match[0].length;
  }

  if (lastIndex < text.length) {
    segments.push(text.slice(lastIndex));
  }

  return segments.length > 0 ? segments : text;
}

function renderInlineRichText(
  text: string,
  maxSourceNum: number,
  onCitationClick: (num: number) => void
) {
  const segments: Array<string | JSX.Element> = [];
  const pattern = /(\*\*[^*]+\*\*)|\[(\d+)\]|【(\d+)】/g;
  let lastIndex = 0;
  let match: RegExpExecArray | null;

  while ((match = pattern.exec(text)) !== null) {
    if (match.index > lastIndex) {
      segments.push(text.slice(lastIndex, match.index));
    }

    if (match[1]) {
      segments.push(<strong key={`rs${segments.length}`}>{match[1].slice(2, -2)}</strong>);
    } else {
      const num = parseInt(match[2] ?? match[3], 10);
      if (num >= 1 && num <= maxSourceNum) {
        segments.push(
          <button
            key={`rs${segments.length}`}
            className="citation-chip"
            type="button"
            onClick={() => onCitationClick(num)}
            aria-label={`跳转到来源 ${num}`}
          >
            [{num}]
          </button>
        );
      } else {
        segments.push(match[0]);
      }
    }

    lastIndex = match.index + match[0].length;
  }

  if (lastIndex < text.length) {
    segments.push(text.slice(lastIndex));
  }

  return segments.length > 0 ? segments : text;
}

function renderRichMarkdown(
  content: string,
  maxSourceNum: number,
  onCitationClick: (num: number) => void
) {
  return content
    .split(/\r?\n/)
    .map((rawLine, index) => {
      const line = rawLine.trim();
      if (!line) return null;
      if (line.startsWith("### ")) return <h3 key={`rh-${index}`}>{line.slice(4)}</h3>;
      if (line.startsWith("## ")) return <h3 key={`rh-${index}`}>{line.slice(3)}</h3>;
      if (line.startsWith("# ")) return <h3 key={`rh-${index}`}>{line.slice(2)}</h3>;
      if (line.startsWith("- ")) return <li key={`rl-${index}`}>{renderInlineRichText(line.slice(2), maxSourceNum, onCitationClick)}</li>;
      return <p key={`rp-${index}`} className="study-paragraph">{renderInlineRichText(line, maxSourceNum, onCitationClick)}</p>;
    })
    .filter(Boolean);
}

function formatTime(isoString: string): string {
  try {
    const hasTimezone = /[Z+\-]\d{2}:\d{2}$|Z$/i.test(isoString);
    const utcDate = new Date(hasTimezone ? isoString : `${isoString}Z`);
    return new Intl.DateTimeFormat("zh-CN", {
      timeZone: "Asia/Shanghai",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      hour12: false
    }).format(utcDate).replace(/\//g, "-");
  } catch {
    return isoString;
  }
}

function formatSourceScope(sourceFilters: string[]): string {
  if (!sourceFilters || sourceFilters.length === 0) return "全科资料";
  const names = sourceFilters.map((path) => path.split("/").pop() || path);
  if (names.length === 1) return names[0];
  if (names.length <= 3) return names.join("、");
  return `${names.slice(0, 2).join("、")} 等 ${names.length} 个资料`;
}

function sanitizeFilenamePart(value: string): string {
  return value
    .replace(/[<>:"/\\|?*]/g, "")
    .replace(/\s+/g, " ")
    .trim();
}

function formatExportTimestamp(date?: Date | string): string {
  const d = date ? new Date(date) : new Date();
  const y = d.getFullYear();
  const M = String(d.getMonth() + 1).padStart(2, "0");
  const D = String(d.getDate()).padStart(2, "0");
  const h = String(d.getHours()).padStart(2, "0");
  const m = String(d.getMinutes()).padStart(2, "0");
  return `${y}${M}${D}_${h}${m}`;
}

function buildExportFilename(opts: {
  subject: string;
  exportType: string;
  scopeLabel: string;
  generatedAt?: string | Date;
  extension: string;
}): string {
  const { subject, exportType, extension } = opts;
  const timestamp = formatExportTimestamp(opts.generatedAt);
  let scope = sanitizeFilenamePart(opts.scopeLabel);
  if (!scope || scope === "全科资料") scope = "全部资料";
  scope = scope.replace(/\.[a-zA-Z0-9]+$/, "");
  const parts = [subject, exportType, scope, timestamp].filter(Boolean);
  let filename = sanitizeFilenamePart(parts.join("_")) + extension;
  if (filename.length > 120) {
    filename = filename.slice(0, 120 - extension.length) + extension;
  }
  return filename;
}

function App() {
  const [subjectOptions, setSubjectOptions] = useState<Subject[]>([]);
  const [selectedSubject, setSelectedSubject] = useState("");
  const [materialItems, setMaterialItems] = useState<Material[]>([]);
  const [subjectStatus, setSubjectStatus] = useState<SubjectStatus | null>(null);
  const [selectedMaterialIds, setSelectedMaterialIds] = useState<string[]>([]);
  const [subjectsLoading, setSubjectsLoading] = useState(true);
  const [subjectDataLoading, setSubjectDataLoading] = useState(false);
  const [isSubjectDropdownOpen, setIsSubjectDropdownOpen] = useState(false);
  const [isAddingSubject, setIsAddingSubject] = useState(false);
  const [newSubjectName, setNewSubjectName] = useState("");
  const [subjectCreateLoading, setSubjectCreateLoading] = useState(false);
  const [subjectCreateError, setSubjectCreateError] = useState("");
  const [apiNotice, setApiNotice] = useState("");
  const [aiSettings, setAiSettings] = useState<AiSettings>(defaultAiSettings);
  const [aiSettingsDraft, setAiSettingsDraft] = useState({
    enabled: defaultAiSettings.enabled,
    provider: defaultAiSettings.provider as AiProvider,
    base_url: defaultAiSettings.base_url,
    model: defaultAiSettings.model,
    api_key: ""
  });
  const [aiSettingsLoading, setAiSettingsLoading] = useState(false);
  const [aiSettingsSaving, setAiSettingsSaving] = useState(false);
  const [aiSettingsNotice, setAiSettingsNotice] = useState("");
  const [showAiSettingsModal, setShowAiSettingsModal] = useState(false);
  const [customModelExpanded, setCustomModelExpanded] = useState(false);
  const subjectDropdownRef = useRef<HTMLDivElement | null>(null);
  const [question, setQuestion] = useState("");
  const [quickQuestions, setQuickQuestions] = useState<string[]>(() => pickQuickQuestions());
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [qaHits, setQaHits] = useState<Hit[]>([]);
  const [qaLoading, setQaLoading] = useState(false);
  const [qaWarning, setQaWarning] = useState("");
  const [qaErrorType, setQaErrorType] = useState<string | undefined>();
  const [historyOpen, setHistoryOpen] = useState(false);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [historyError, setHistoryError] = useState("");
  const [historyItems, setHistoryItems] = useState<QAHistoryItem[]>([]);
  const [expandedHistoryId, setExpandedHistoryId] = useState<number | null>(null);
  const [deletingHistoryId, setDeletingHistoryId] = useState<number | null>(null);
  const [expandedEvidenceKeys, setExpandedEvidenceKeys] = useState<Record<string, boolean>>({});
  const [indexLoadingMode, setIndexLoadingMode] = useState<"update" | "reset" | null>(null);
  const [uploadFiles, setUploadFiles] = useState<File[]>([]);
  const [uploadLoading, setUploadLoading] = useState(false);
  const [uploadNotice, setUploadNotice] = useState("");
  const [deletingMaterialId, setDeletingMaterialId] = useState<string | null>(null);
  const [renamingMaterialId, setRenamingMaterialId] = useState<string | null>(null);
  const [convertingMaterialId, setConvertingMaterialId] = useState<string | null>(null);
  const [overviewContent, setOverviewContent] = useState("");
  const [overviewSources, setOverviewSources] = useState<OverviewSource[]>([]);
  const [overviewReferences, setOverviewReferences] = useState<OverviewSource[]>([]);
  const [overviewCached, setOverviewCached] = useState(false);
  const [overviewWarning, setOverviewWarning] = useState("");
  const [overviewLoading, setOverviewLoading] = useState(false);
  const [studyGuideContent, setStudyGuideContent] = useState("");
  const [studyGuideSources, setStudyGuideSources] = useState<StudyGuideSource[]>([]);
  const [studyGuideReferences, setStudyGuideReferences] = useState<StudyGuideSource[]>([]);
  const [studyGuideCached, setStudyGuideCached] = useState(false);
  const [studyGuideWarning, setStudyGuideWarning] = useState("");
  const [studyGuideLoading, setStudyGuideLoading] = useState(false);
  const [sourcesExpanded, setSourcesExpanded] = useState(false);
  const [overviewSourcesExpanded, setOverviewSourcesExpanded] = useState(false);
  const [studyGuideSourcesExpanded, setStudyGuideSourcesExpanded] = useState(false);
  const evidenceCardRefs = useRef<Record<number, HTMLArticleElement | null>>({});
  const highlightTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const originalTitleRef = useRef(document.title);
  const [highlightedEvidenceIndex, setHighlightedEvidenceIndex] = useState<number | null>(null);
  const overviewCardRefs = useRef<Record<number, HTMLDivElement | null>>({});
  const studyGuideCardRefs = useRef<Record<number, HTMLDivElement | null>>({});
  const [highlightedOverviewIndex, setHighlightedOverviewIndex] = useState<number | null>(null);
  const [highlightedStudyGuideIndex, setHighlightedStudyGuideIndex] = useState<number | null>(null);
  const [highlightedSelfTestSourceIndex, setHighlightedSelfTestSourceIndex] = useState<number | null>(null);
  const [previewHitIndex, setPreviewHitIndex] = useState<number | null>(null);
  const [previewReferenceTarget, setPreviewReferenceTarget] = useState<PreviewReferenceTarget | null>(null);
  const [previewReferenceList, setPreviewReferenceList] = useState<PreviewReferenceTarget[]>([]);
  const [previewReferenceIndex, setPreviewReferenceIndex] = useState<number | null>(null);
  const [isPreviewSnippetExpanded, setIsPreviewSnippetExpanded] = useState(false);
  const [snippetKeywordCache, setSnippetKeywordCache] = useState<Record<string, string[]>>({});
  const [imageError, setImageError] = useState(false);
  const [previewNotice, setPreviewNotice] = useState("");
  const [previewZoom, setPreviewZoom] = useState<"fit-width" | number>("fit-width");
  const [activeInsightTab, setActiveInsightTab] = useState<"overview" | "guide">("overview");
  const [materialSelectionMode, setMaterialSelectionMode] = useState<"materials" | "groups">("materials");
  const [selectedGroupNames, setSelectedGroupNames] = useState<string[]>([]);
  const [expandedGroupNames, setExpandedGroupNames] = useState<string[]>([]);
  const [sidebarWidth, setSidebarWidth] = useState(() =>
    readStoredDimension(
      SIDEBAR_WIDTH_STORAGE_KEY,
      SIDEBAR_WIDTH_DEFAULT,
      SIDEBAR_WIDTH_MIN,
      SIDEBAR_WIDTH_MAX
    )
  );
  const [isSidebarCollapsed, setIsSidebarCollapsed] = useState(() =>
    readStoredBoolean(SIDEBAR_COLLAPSED_STORAGE_KEY, false)
  );
  const [insightHeight, setInsightHeight] = useState(() =>
    readStoredDimension(
      INSIGHT_HEIGHT_STORAGE_KEY,
      INSIGHT_HEIGHT_DEFAULT,
      INSIGHT_HEIGHT_MIN,
      INSIGHT_HEIGHT_MAX
    )
  );
  const [activeResize, setActiveResize] = useState<"sidebar" | "insight" | null>(null);
  const qaInputRef = useRef<HTMLTextAreaElement | null>(null);
  const askFormRef = useRef<HTMLFormElement | null>(null);
  const [selfTestResult, setSelfTestResult] = useState<SelfTestResult | null>(null);
  const [showQuizModal, setShowQuizModal] = useState(false);
  const [quizSettings, setQuizSettings] = useState<QuizSettings>({
    typeConfigs: [
      { type: "choice", enabled: true, count: 3 },
      { type: "fill", enabled: true, count: 3 },
      { type: "essay", enabled: true, count: 2 },
    ],
    answerMode: "dual"
  });
  const [activeQuizTab, setActiveQuizTab] = useState<"practice" | "answer">("practice");
  const [showAnswer, setShowAnswer] = useState(false);
  const [dragIndex, setDragIndex] = useState<number | null>(null);
  const [dragOverIndex, setDragOverIndex] = useState<number | null>(null);
  const [selfTestSourcesExpanded, setSelfTestSourcesExpanded] = useState(false);
  const [selfTestIncludeSources, setSelfTestIncludeSources] = useState(false);
  const [qaIncludeSources, setQaIncludeSources] = useState(false);
  const [overviewIncludeSources, setOverviewIncludeSources] = useState(false);
  const [studyGuideIncludeSources, setStudyGuideIncludeSources] = useState(false);
  const [printPayload, setPrintPayload] = useState<PrintPayload | null>(null);
  const isSelfTestRequestRef = useRef(false);
  const selfTestSourceRefs = useRef<Record<number, HTMLElement | null>>({});
  const [longformSettingsOpen, setLongformSettingsOpen] = useState(false);
  const [longformLoading, setLongformLoading] = useState(false);
  const [longformError, setLongformError] = useState("");
  const [longformResult, setLongformResult] = useState<LongformResponse | null>(null);
  const [longformType, setLongformType] = useState<LongformType>("analysis");
  const [longformTargetLength, setLongformTargetLength] = useState(3000);
  const [longformIncludeSources, setLongformIncludeSources] = useState(true);
  const [longformInstruction, setLongformInstruction] = useState("");
  const [longformOutlineExpanded, setLongformOutlineExpanded] = useState(false);
  const [longformSummariesExpanded, setLongformSummariesExpanded] = useState(false);
  const [longformSourcesExpanded, setLongformSourcesExpanded] = useState(false);
  const [longformCustomLength, setLongformCustomLength] = useState("");

  const materialGroups = useMemo(() => {
    const groups = new Map<string, Material[]>();
    materialItems.forEach((material) => {
      const groupName = material.group || material.chapter || "其他资料";
      const groupItems = groups.get(groupName) || [];
      groupItems.push(material);
      groups.set(groupName, groupItems);
    });
    return Array.from(groups, ([name, materials]) => ({ name, materials }));
  }, [materialItems]);

  const selectedMaterials = useMemo(() => {
    if (materialSelectionMode === "groups") {
      if (!selectedGroupNames.length) {
        return [];
      }
      const selectedGroups = new Set(selectedGroupNames);
      return materialItems.filter((material) =>
        selectedGroups.has(material.group || material.chapter || "其他资料")
      );
    }

    return materialItems.filter((material) => selectedMaterialIds.includes(material.id));
  }, [materialItems, materialSelectionMode, selectedGroupNames, selectedMaterialIds]);

  useEffect(() => {
    let ignored = false;

    async function loadSubjects() {
      setSubjectsLoading(true);
      try {
        const apiSubjects = await fetchSubjects();
        if (ignored) {
          return;
        }
        const nextSubjects = apiSubjects.length ? apiSubjects : mockSubjects;
        setSubjectOptions(nextSubjects);

        // Restore last selected subject from localStorage
        let restoreSubject = "";
        try {
          const saved = window.localStorage.getItem(LAST_SUBJECT_STORAGE_KEY);
          if (saved && nextSubjects.some((s) => s.name === saved)) {
            restoreSubject = saved;
          }
        } catch {
          // ignore
        }
        if (!restoreSubject) {
          restoreSubject = nextSubjects.find((s) => s.name === DEFAULT_SUBJECT)?.name || nextSubjects[0]?.name || "";
        }
        setSelectedSubject(restoreSubject);
        setApiNotice(apiSubjects.length ? "" : "后端暂未返回科目，已显示示例数据。");
      } catch (error) {
        console.error("GET /api/subjects failed", error);
        if (ignored) {
          return;
        }
        setSubjectOptions(mockSubjects);
        const saved = (() => { try { return window.localStorage.getItem(LAST_SUBJECT_STORAGE_KEY); } catch { return null; } })();
        const restoreSubject = saved && mockSubjects.some((s) => s.name === saved)
          ? saved
          : mockSubjects.find((s) => s.name === DEFAULT_SUBJECT)?.name || mockSubjects[0]?.name || "";
        setSelectedSubject(restoreSubject);
        setApiNotice(`GET /api/subjects 失败：${errorMessage(error)}。当前显示 mock 示例数据。`);
      } finally {
        if (!ignored) {
          setSubjectsLoading(false);
        }
      }
    }

    loadSubjects();
    return () => {
      ignored = true;
    };
  }, []);

  useEffect(() => {
    if (!selectedSubject) {
      return;
    }

    let ignored = false;

    function resetQaSessionState() {
      if (highlightTimerRef.current) {
        clearTimeout(highlightTimerRef.current);
        highlightTimerRef.current = null;
      }
      setQuestion("");
      setMessages([]);
      setQaHits([]);
      setQaLoading(false);
      setQaWarning("");
      setQaErrorType(undefined);
      setExpandedEvidenceKeys({});
      setSourcesExpanded(false);
      setHighlightedEvidenceIndex(null);
      setPreviewNotice("");
      setSnippetKeywordCache({});
      setSelfTestResult(null);
      setShowQuizModal(false);
      setActiveQuizTab("practice");
      setShowAnswer(false);
      setSelfTestSourcesExpanded(false);
      setLongformResult(null);
      setLongformLoading(false);
      setLongformError("");
      setLongformSettingsOpen(false);
      setLongformOutlineExpanded(false);
      setLongformSummariesExpanded(false);
      setLongformSourcesExpanded(false);
      closeSourcePreview();
    }

    resetQaSessionState();

    async function loadSubjectData() {
      setSubjectDataLoading(true);
      setQaWarning("");
      setQaErrorType(undefined);
      const materialsEndpoint = `/api/subjects/${encodeURIComponent(selectedSubject)}/materials`;
      const statusEndpoint = `/api/subjects/${encodeURIComponent(selectedSubject)}/status`;

      const [materialsResult, statusResult] = await Promise.allSettled([
        fetchSubjectMaterials(selectedSubject),
        fetchSubjectStatus(selectedSubject)
      ]);

      if (ignored) {
        return;
      }

      const notices: string[] = [];

      if (materialsResult.status === "fulfilled") {
        setMaterialItems(materialsResult.value);
        setSelectedMaterialIds([]);
        setSelectedGroupNames([]);
        setExpandedGroupNames([]);
      } else {
        console.error(`GET ${materialsEndpoint} failed`, materialsResult.reason);
        setMaterialItems(mockMaterials);
        setSelectedMaterialIds([]);
        setSelectedGroupNames([]);
        setExpandedGroupNames([]);
        notices.push(`GET ${materialsEndpoint} 失败：${errorMessage(materialsResult.reason)}`);
      }

      if (statusResult.status === "fulfilled") {
        setSubjectStatus(statusResult.value);
        if (statusResult.value.index_status === "corrupted" && statusResult.value.warning) {
          notices.push(`后端提示：${statusResult.value.warning}`);
        } else if (hasReadyIndex(statusResult.value)) {
          setQaWarning("");
          setQaErrorType(undefined);
        }
      } else {
        console.error(`GET ${statusEndpoint} failed`, statusResult.reason);
        setSubjectStatus({
          subject: selectedSubject,
          file_count:
            materialsResult.status === "fulfilled" ? materialsResult.value.length : mockMaterials.length,
          total_size_bytes: 0,
          indexed_count: 0,
          deepseek_configured: false,
          materials_dir: "",
          outputs_dir: ""
        });
        notices.push(`GET ${statusEndpoint} 失败：${errorMessage(statusResult.reason)}`);
      }

      setApiNotice(notices.length ? `${notices.join("；")}。已保留可用数据并回退 mock。` : "");
      setSubjectDataLoading(false);
    }

    loadSubjectData();
    return () => {
      ignored = true;
    };
  }, [selectedSubject]);

  useEffect(() => {
    if (!isSubjectDropdownOpen) {
      return;
    }

    const handlePointerDown = (event: MouseEvent) => {
      if (!subjectDropdownRef.current?.contains(event.target as Node)) {
        setIsSubjectDropdownOpen(false);
      }
    };
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setIsSubjectDropdownOpen(false);
      }
    };

    document.addEventListener("mousedown", handlePointerDown);
    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.removeEventListener("mousedown", handlePointerDown);
      document.removeEventListener("keydown", handleKeyDown);
    };
  }, [isSubjectDropdownOpen]);

  const toggleMaterial = (id: string) => {
    setSelectedMaterialIds((current) =>
      current.includes(id) ? current.filter((item) => item !== id) : [...current, id]
    );
  };

  const toggleMaterialGroup = (name: string) => {
    setSelectedGroupNames((current) =>
      current.includes(name) ? current.filter((item) => item !== name) : [...current, name]
    );
  };

  const toggleMaterialGroupExpanded = (name: string) => {
    setExpandedGroupNames((current) =>
      current.includes(name) ? current.filter((item) => item !== name) : [...current, name]
    );
  };

  const switchMaterialSelectionMode = (mode: "materials" | "groups") => {
    setMaterialSelectionMode(mode);
    if (mode === "materials") {
      setSelectedGroupNames([]);
    } else {
      setSelectedMaterialIds([]);
    }
  };

  const handleCreateSubject = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const trimmedName = newSubjectName.replace(/\s+/g, " ").trim();
    if (!trimmedName) {
      setSubjectCreateError("科目名称不能为空。");
      return;
    }
    if (trimmedName.includes("/") || trimmedName.includes("\\")) {
      setSubjectCreateError("科目名称不能包含 / 或 \\。");
      return;
    }
    if (trimmedName.includes("..")) {
      setSubjectCreateError("科目名称不能包含 '..'。");
      return;
    }
    if (subjectOptions.some((subject) => subject.name === trimmedName)) {
      setSubjectCreateError("科目已存在。");
      return;
    }

    setSubjectCreateLoading(true);
    setSubjectCreateError("");
    try {
      const created = await createSubject(trimmedName);
      const refreshedSubjects = await fetchSubjects().catch((error) => {
        console.error("GET /api/subjects after create failed", error);
        return [];
      });
      const nextSubjects = refreshedSubjects.length
        ? refreshedSubjects
        : [...subjectOptions.filter((subject) => subject.name !== created.name), { name: created.name }];

      setSubjectOptions(nextSubjects);
      setSelectedSubject(created.name);
      setMaterialItems([]);
      setSelectedMaterialIds([]);
      setSelectedGroupNames([]);
      setExpandedGroupNames([]);
      setMessages([]);
      setQaHits([]);
      setQaWarning("");
      setQaErrorType(undefined);
      setSubjectStatus({
        subject: created.name,
        file_count: 0,
        total_size_bytes: 0,
        indexed_count: 0,
        index_status: "empty",
        deepseek_configured: subjectStatus?.deepseek_configured ?? false,
        materials_dir: created.materials_dir || "",
        outputs_dir: created.outputs_dir || ""
      });
      setNewSubjectName("");
      setIsAddingSubject(false);
      setApiNotice(`已创建科目：${created.name}`);
    } catch (error) {
      console.error("POST /api/subjects failed", error);
      setSubjectCreateError(errorMessage(error));
    } finally {
      setSubjectCreateLoading(false);
    }
  };

  const evidenceItems = useMemo(
    () => qaHits.map((hit, index) => ({
      hit,
      index,
      source: hitToSourceReference(hit, index)
    })),
    [qaHits]
  );
  const relatedPreviewHits = useMemo(() => {
    const previews: Array<{
      hit: Hit;
      index: number;
      imageUrl: string;
      fileName: string;
      fileType: PreviewFileType;
      pageNumber: number;
    }> = [];

    for (const [index, hit] of qaHits.entries()) {
      const imageUrl = buildPageImageUrl(selectedSubject, hit);
      const fileType = getHitFileType(hit);
      const pageNumber = getHitPreviewPageNumber(hit);

      if (!imageUrl || !fileType || pageNumber === null) {
        continue;
      }

      previews.push({
        hit,
        index,
        imageUrl,
        fileName:
          metadataText(hit.metadata, "file_name") ||
          metadataText(hit.metadata, "source_path") ||
          "未知来源",
        fileType,
        pageNumber
      });

      if (previews.length >= 2) {
        break;
      }
    }

    return previews;
  }, [qaHits, selectedSubject]);
  const overviewReferencePreviews = useMemo(
    () => buildReferencePreviews(selectedSubject, overviewReferences),
    [overviewReferences, selectedSubject]
  );
  const studyGuideReferencePreviews = useMemo(
    () => buildReferencePreviews(selectedSubject, studyGuideReferences),
    [selectedSubject, studyGuideReferences]
  );
  const overviewSourcePreviewTargets = useMemo(
    () => buildSourcePreviewTargets(overviewSources),
    [overviewSources]
  );
  const studyGuideSourcePreviewTargets = useMemo(
    () => buildSourcePreviewTargets(studyGuideSources),
    [studyGuideSources]
  );
  const sourceItems = evidenceItems.map((item) => item.source);
  const evidenceTitle = `本次回答的 ${sourceItems.length} 个检索依据`;
  const previewHit =
    previewHitIndex !== null && previewHitIndex >= 0 && previewHitIndex < qaHits.length
      ? qaHits[previewHitIndex]
      : null;
  const previewMetadata: Record<string, unknown> = previewHit?.metadata ?? {};
  const previewSourcePath = previewHit
    ? metadataText(previewMetadata, "source_path")
    : previewReferenceTarget?.sourcePath || "";
  const previewFileName = previewHit
    ? metadataText(previewMetadata, "file_name") || previewSourcePath || "未知来源"
    : previewReferenceTarget?.fileName || "";
  const previewRawFileType = previewHit
    ? metadataText(previewMetadata, "file_type")
    : previewReferenceTarget?.fileType.toUpperCase() || "";
  const previewFileType = previewHit ? getHitFileType(previewHit) : previewReferenceTarget?.fileType || null;
  const previewPageNumber = previewHit
    ? metadataNumber(previewMetadata, "page_number")
    : previewReferenceTarget?.fileType === "pdf"
      ? previewReferenceTarget.pageNumber
      : null;
  const previewSlideNumber = previewHit
    ? metadataNumber(previewMetadata, "slide_number")
    : previewReferenceTarget && previewReferenceTarget.fileType !== "pdf"
      ? previewReferenceTarget.pageNumber
      : null;
  const previewLocation =
    previewHit
      ? metadataText(previewMetadata, "location") ||
        (previewPageNumber !== null
          ? `第 ${Math.trunc(previewPageNumber)} 页`
          : previewSlideNumber !== null
            ? `第 ${Math.trunc(previewSlideNumber)} 张幻灯片`
            : metadataText(previewMetadata, "chunk_index")
            ? `chunk ${metadataText(previewMetadata, "chunk_index")}`
            : "位置未知")
      : previewReferenceTarget
        ? previewLocationText(previewReferenceTarget.fileType, previewReferenceTarget.pageNumber)
      : "";
  const previewCanShowDocumentPage = previewHit ? canPreviewHit(previewHit) : Boolean(previewReferenceTarget);
  const previewImageMessage = previewHit || previewReferenceTarget
    ? previewFileType
      ? previewCanShowDocumentPage
        ? ""
        : "该来源暂不支持页面预览"
      : "该文件类型暂不支持页面预览"
    : "";
  const previewEvidenceLevel =
    previewHitIndex !== null ? getEvidenceLevel(previewHitIndex) : { text: "", className: "" };
  const previewSnippet = previewHit
    ? previewHit.text || "暂无命中文本片段。"
    : previewReferenceTarget?.snippet || "暂无命中文本片段。";
  const previewSnippetText = cleanPreviewSnippet(previewSnippet);
  const previewRuleHighlightTerms = extractHighlightTerms(previewSnippetText);
  const previewAiHighlightTerms = previewSnippetText ? snippetKeywordCache[previewSnippetText] : undefined;
  const previewHighlightTerms = previewAiHighlightTerms?.length ? previewAiHighlightTerms : previewRuleHighlightTerms;
  const hasPreviewSnippetText = Boolean(previewSnippetText);
  const previewSnippetTitle = previewFileType === "ppt" || previewFileType === "pptx"
    ? "🎯 本页提及"
    : "🎯 本页依据";
  const sourcePreviewOpen = Boolean(previewHit || previewReferenceTarget);
  const currentScopeText =
    materialSelectionMode === "groups" && selectedGroupNames.length
      ? `${selectedGroupNames.length} 个章节 · ${selectedMaterials.length} 个资料`
      : selectedMaterials.length
        ? `${selectedMaterials.length} 个资料`
        : "全部资料";
  const qaScopeCapsuleText =
    materialSelectionMode === "groups" && selectedGroupNames.length
      ? `当前受控知识库范围：已选 ${selectedGroupNames.length} 个章节 / ${selectedMaterials.length} 份资料`
      : selectedMaterials.length
        ? `当前受控知识库范围：已选 ${selectedMaterials.length} 份资料`
        : "当前受控知识库范围：全部资料";
  const aiDisabledHint = "AI 已关闭，仍可查看资料来源和页面预览。";
  const selectionCountText =
    materialSelectionMode === "groups"
      ? `${selectedGroupNames.length} 组已选`
      : `${selectedMaterialIds.length} 个已选`;
  const selectedSourceFilters = selectedMaterials.map((material) => material.relativePath);

  // Persist selected subject to localStorage
  useEffect(() => {
    if (!selectedSubject) return;
    try {
      window.localStorage.setItem(LAST_SUBJECT_STORAGE_KEY, selectedSubject);
    } catch {
      // ignore
    }
  }, [selectedSubject]);

  // Persist source filters per subject
  useEffect(() => {
    if (!selectedSubject || !subjectStatus?.indexed_count) return;
    try {
      window.localStorage.setItem(
        `${LAST_SOURCE_FILTERS_KEY}:${selectedSubject}`,
        JSON.stringify(selectedSourceFilters),
      );
    } catch {
      // ignore
    }
  }, [selectedSubject, selectedSourceFilters, subjectStatus?.indexed_count]);

  // Restore source filters on first load of a subject (runs when materials first become available)
  const hasRestoredFiltersRef = useRef<string>("");
  useEffect(() => {
    if (!selectedSubject || !materialItems.length || !subjectStatus?.indexed_count) return;
    if (hasRestoredFiltersRef.current === selectedSubject) return;
    hasRestoredFiltersRef.current = selectedSubject;

    try {
      const saved = window.localStorage.getItem(`${LAST_SOURCE_FILTERS_KEY}:${selectedSubject}`);
      if (!saved) return;

      const savedPaths: string[] = JSON.parse(saved);
      if (!Array.isArray(savedPaths) || !savedPaths.length) return;

      const availablePaths = new Set(materialItems.map((m) => m.relativePath));
      const validPaths = savedPaths.filter((p) => availablePaths.has(p));

      if (validPaths.length === 0) return;

      const validIds = materialItems
        .filter((m) => validPaths.includes(m.relativePath))
        .map((m) => m.id);

      if (validIds.length && materialSelectionMode === "materials") {
        setSelectedMaterialIds(validIds);
      }
    } catch {
      // ignore parse errors
    }
  }, [selectedSubject, materialItems, subjectStatus?.indexed_count, materialSelectionMode]);

  const indexCorrupted = subjectStatus?.index_status === "corrupted" || qaErrorType === "index_corrupted";
  const indexCorruptedHint = "索引异常时请使用“重建当前范围”";
  const qaLoadingText = "正在查找相关课程资料并定位复习依据…";
  const previewImageUrl = previewHit
    ? buildPageImageUrl(selectedSubject, previewHit)
    : previewReferenceTarget
      ? buildPageImageUrl(selectedSubject, previewReferenceTarget.sourcePath, previewReferenceTarget.pageNumber)
      : "";
  const workspaceStyle = {
    "--insight-height": `${insightHeight}px`
  } as CSSProperties;
  const sidebarStyle = {
    width: isSidebarCollapsed ? 0 : `${sidebarWidth}px`
  } as CSSProperties;
  const workspaceClassName = [
    "workspace",
    isSidebarCollapsed ? "sidebar-collapsed" : "",
    activeResize ? "is-resizing" : "",
    activeResize === "sidebar" ? "resizing-sidebar" : "",
    activeResize === "insight" ? "resizing-insight" : ""
  ].filter(Boolean).join(" ");

  const isSmallWorkbench = () =>
    typeof window !== "undefined" && window.matchMedia(`(max-width: ${RESIZE_BREAKPOINT}px)`).matches;

  const handleSidebarResizeStart = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (isSidebarCollapsed || isSmallWorkbench()) {
      return;
    }

    event.preventDefault();
    event.currentTarget.setPointerCapture?.(event.pointerId);

    const startX = event.clientX;
    const startWidth = sidebarWidth;
    let nextWidth = startWidth;
    const previousCursor = document.body.style.cursor;
    const previousUserSelect = document.body.style.userSelect;

    setActiveResize("sidebar");
    document.body.style.cursor = "col-resize";
    document.body.style.userSelect = "none";

    const handlePointerMove = (moveEvent: PointerEvent) => {
      nextWidth = clampNumber(
        startWidth + moveEvent.clientX - startX,
        SIDEBAR_WIDTH_MIN,
        SIDEBAR_WIDTH_MAX
      );
      setSidebarWidth(nextWidth);
    };

    const stopResize = () => {
      window.removeEventListener("pointermove", handlePointerMove);
      window.removeEventListener("pointerup", stopResize);
      window.removeEventListener("pointercancel", stopResize);
      document.body.style.cursor = previousCursor;
      document.body.style.userSelect = previousUserSelect;
      setActiveResize(null);
      saveStoredDimension(SIDEBAR_WIDTH_STORAGE_KEY, nextWidth);
    };

    window.addEventListener("pointermove", handlePointerMove);
    window.addEventListener("pointerup", stopResize);
    window.addEventListener("pointercancel", stopResize);
  };

  const collapseSidebar = () => {
    setIsSidebarCollapsed(true);
    saveStoredBoolean(SIDEBAR_COLLAPSED_STORAGE_KEY, true);
  };

  const expandSidebar = () => {
    setIsSidebarCollapsed(false);
    saveStoredBoolean(SIDEBAR_COLLAPSED_STORAGE_KEY, false);
  };

  const handleInsightResizeStart = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (isSmallWorkbench()) {
      return;
    }

    event.preventDefault();
    event.currentTarget.setPointerCapture?.(event.pointerId);

    const startY = event.clientY;
    const startHeight = insightHeight;
    let nextHeight = startHeight;
    const previousCursor = document.body.style.cursor;
    const previousUserSelect = document.body.style.userSelect;

    setActiveResize("insight");
    document.body.style.cursor = "row-resize";
    document.body.style.userSelect = "none";

    const handlePointerMove = (moveEvent: PointerEvent) => {
      nextHeight = clampNumber(
        startHeight + moveEvent.clientY - startY,
        INSIGHT_HEIGHT_MIN,
        INSIGHT_HEIGHT_MAX
      );
      setInsightHeight(nextHeight);
    };

    const stopResize = () => {
      window.removeEventListener("pointermove", handlePointerMove);
      window.removeEventListener("pointerup", stopResize);
      window.removeEventListener("pointercancel", stopResize);
      document.body.style.cursor = previousCursor;
      document.body.style.userSelect = previousUserSelect;
      setActiveResize(null);
      saveStoredDimension(INSIGHT_HEIGHT_STORAGE_KEY, nextHeight);
    };

    window.addEventListener("pointermove", handlePointerMove);
    window.addEventListener("pointerup", stopResize);
    window.addEventListener("pointercancel", stopResize);
  };

  useEffect(() => {
    let cancelled = false;
    setAiSettingsLoading(true);
    fetchAiSettings()
      .then((settings) => {
        if (cancelled) {
          return;
        }
        setAiSettings(settings);
        setAiSettingsDraft({
          enabled: settings.enabled,
          provider: settings.provider,
          base_url: settings.base_url,
          model: settings.model,
          api_key: ""
        });
        setAiSettingsNotice("");
      })
      .catch((error) => {
        if (!cancelled) {
          setAiSettingsNotice(`AI 设置加载失败：${errorMessage(error)}`);
        }
      })
      .finally(() => {
        if (!cancelled) {
          setAiSettingsLoading(false);
        }
      });

    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    return () => {
      if (highlightTimerRef.current) {
        clearTimeout(highlightTimerRef.current);
        highlightTimerRef.current = null;
      }
    };
  }, []);

  useEffect(() => {
    const afterPrint = () => {
      document.title = originalTitleRef.current;
      setPrintPayload(null);
    };
    window.addEventListener("afterprint", afterPrint);
    return () => window.removeEventListener("afterprint", afterPrint);
  }, []);

  useEffect(() => {
    if (printPayload) {
      originalTitleRef.current = document.title;
      document.title = printPayload.filename || printPayload.title;
      const timer = setTimeout(() => window.print(), 80);
      return () => {
        document.title = originalTitleRef.current;
        clearTimeout(timer);
      };
    }
  }, [printPayload]);

  useEffect(() => {
    if (!sourcePreviewOpen || !previewSnippetText || snippetKeywordCache[previewSnippetText]) {
      return;
    }

    let cancelled = false;
    fetchSnippetKeywords({ text: previewSnippetText })
      .then((result) => {
        if (cancelled) {
          return;
        }
        setSnippetKeywordCache((current) => {
          if (current[previewSnippetText]) {
            return current;
          }
          return {
            ...current,
            [previewSnippetText]: normalizeSnippetKeywords(previewSnippetText, result.keywords || [])
          };
        });
      })
      .catch(() => {
        if (cancelled) {
          return;
        }
        setSnippetKeywordCache((current) => current[previewSnippetText] ? current : {
          ...current,
          [previewSnippetText]: []
        });
      });

    return () => {
      cancelled = true;
    };
  }, [sourcePreviewOpen, previewSnippetText, snippetKeywordCache]);

  useEffect(() => {
    if (messages.length === 0) {
      setQuickQuestions(pickQuickQuestions());
    }
  }, [messages.length]);

  useEffect(() => {
    setOverviewContent("");
    setOverviewSources([]);
    setOverviewReferences([]);
    setOverviewCached(false);
    setOverviewWarning("");
    setStudyGuideContent("");
    setStudyGuideSources([]);
    setStudyGuideReferences([]);
    setStudyGuideCached(false);
    setStudyGuideWarning("");
  }, [selectedSubject, selectedMaterialIds, selectedGroupNames, materialSelectionMode]);

  useEffect(() => {
    const availableGroups = new Set(materialGroups.map((group) => group.name));
    setSelectedGroupNames((current) => current.filter((name) => availableGroups.has(name)));
    setExpandedGroupNames((current) => current.filter((name) => availableGroups.has(name)));
  }, [materialGroups]);

  const makeBuildPayload = (mode: "update" | "reset"): BuildIndexRequest => {
    const files = selectedMaterials.map((material) => material.relativePath);
    return {
      mode,
      scope: files.length ? "selected" : "all",
      files
    };
  };

  const handleUploadFileChange = (event: ChangeEvent<HTMLInputElement>) => {
    setUploadFiles(Array.from(event.target.files || []));
    setUploadNotice("");
  };

  const handleUploadMaterials = async () => {
    if (!selectedSubject) {
      setUploadNotice("请先选择科目。");
      return;
    }
    if (!uploadFiles.length) {
      setUploadNotice("请先选择要上传的资料。");
      return;
    }

    const unsupported = uploadFiles.filter((file) => {
      const lowerName = file.name.toLowerCase();
      return !allowedUploadExtensions.some((extension) => lowerName.endsWith(extension));
    });
    if (unsupported.length) {
      setUploadNotice(`不支持的文件格式：${unsupported.map((file) => file.name).join("；")}`);
      return;
    }

    setUploadLoading(true);
    setUploadNotice("");
    try {
      const result = await uploadMaterials(selectedSubject, uploadFiles);
      if (!result.success) {
        setUploadNotice(result.warning || result.error || "上传失败，请检查后端日志。");
        return;
      }

      const [materialsResult, statusResult] = await Promise.allSettled([
        fetchSubjectMaterials(selectedSubject),
        fetchSubjectStatus(selectedSubject)
      ]);

      if (materialsResult.status === "fulfilled") {
        const nextMaterials = materialsResult.value;
        const nextMaterialIds = new Set(nextMaterials.map((material) => material.id));
        setMaterialItems(nextMaterials);
        setSelectedMaterialIds((current) => current.filter((id) => nextMaterialIds.has(id)));
      } else {
        console.error(`GET /api/subjects/${encodeURIComponent(selectedSubject)}/materials failed`, materialsResult.reason);
      }

      if (statusResult.status === "fulfilled") {
        setSubjectStatus(statusResult.value);
      } else {
        console.error(`GET /api/subjects/${encodeURIComponent(selectedSubject)}/status failed`, statusResult.reason);
      }

      setUploadFiles([]);
      setUploadNotice("上传成功，请点击添加/更新当前范围到知识库。");
      setApiNotice(result.message || "上传成功，请添加/更新知识库。");
    } catch (error) {
      console.error("POST /materials/upload failed", error);
      setUploadNotice(`上传接口调用失败：${errorMessage(error)}`);
    } finally {
      setUploadLoading(false);
    }
  };

  const handleDeleteMaterial = async (material: Material) => {
    if (!selectedSubject || deletingMaterialId || renamingMaterialId || convertingMaterialId) {
      return;
    }

    const confirmed = window.confirm("确定要移除这个资料吗？资料会移动到回收目录，不会永久删除。");
    if (!confirmed) {
      return;
    }

    setDeletingMaterialId(material.id);
    setApiNotice("");
    try {
      const result = await deleteMaterial(selectedSubject, material.relativePath);
      if (!result.success) {
        setApiNotice(result.warning || result.error || "资料移除失败，请检查后端日志。");
        return;
      }

      const [materialsResult, statusResult] = await Promise.allSettled([
        fetchSubjectMaterials(selectedSubject),
        fetchSubjectStatus(selectedSubject)
      ]);

      if (materialsResult.status === "fulfilled") {
        const nextMaterials = materialsResult.value;
        const nextMaterialIds = new Set(nextMaterials.map((item) => item.id));
        setMaterialItems(nextMaterials);
        setSelectedMaterialIds((current) => current.filter((id) => nextMaterialIds.has(id)));
      } else {
        console.error(
          `GET /api/subjects/${encodeURIComponent(selectedSubject)}/materials failed`,
          materialsResult.reason
        );
      }

      if (statusResult.status === "fulfilled") {
        setSubjectStatus(statusResult.value);
      } else {
        console.error(`GET /api/subjects/${encodeURIComponent(selectedSubject)}/status failed`, statusResult.reason);
      }

      setApiNotice("资料已移除，请添加/更新知识库。");
    } catch (error) {
      console.error("POST /materials/delete failed", error);
      setApiNotice(`资料移除接口调用失败：${errorMessage(error)}`);
    } finally {
      setDeletingMaterialId(null);
    }
  };

  const handleRenameMaterial = async (material: Material) => {
    if (!selectedSubject || deletingMaterialId || renamingMaterialId || convertingMaterialId) {
      return;
    }

    const input = window.prompt("请输入新的资料文件名：", material.fileName);
    if (input === null) {
      return;
    }

    const newName = input.trim();
    if (!newName) {
      setApiNotice("新文件名不能为空。");
      return;
    }

    const lastSlashIndex = Math.max(material.relativePath.lastIndexOf("/"), material.relativePath.lastIndexOf("\\"));
    const basePath = lastSlashIndex >= 0 ? `${material.relativePath.slice(0, lastSlashIndex)}/` : "";
    const newRelativePath = newName.includes("/") || newName.includes("\\")
      ? newName.replace(/\\/g, "/")
      : `${basePath}${newName}`;

    if (newRelativePath === material.relativePath) {
      setApiNotice("文件名未变化。");
      return;
    }

    setRenamingMaterialId(material.id);
    setApiNotice("");
    try {
      const result = await renameMaterial(selectedSubject, material.relativePath, newRelativePath);
      if (!result.success) {
        setApiNotice(result.warning || result.error || "资料重命名失败，请检查后端日志。");
        return;
      }

      const [materialsResult, statusResult] = await Promise.allSettled([
        fetchSubjectMaterials(selectedSubject),
        fetchSubjectStatus(selectedSubject)
      ]);

      if (materialsResult.status === "fulfilled") {
        const nextMaterials = materialsResult.value;
        const nextMaterialIds = new Set(nextMaterials.map((item) => item.id));
        setMaterialItems(nextMaterials);
        setSelectedMaterialIds((current) =>
          current.filter((id) => id !== material.id && nextMaterialIds.has(id))
        );
      } else {
        console.error(
          `GET /api/subjects/${encodeURIComponent(selectedSubject)}/materials failed`,
          materialsResult.reason
        );
        setSelectedMaterialIds((current) => current.filter((id) => id !== material.id));
      }

      if (statusResult.status === "fulfilled") {
        setSubjectStatus(statusResult.value);
      } else {
        console.error(`GET /api/subjects/${encodeURIComponent(selectedSubject)}/status failed`, statusResult.reason);
      }

      setApiNotice("资料已重命名，请添加/更新知识库。");
    } catch (error) {
      console.error("POST /materials/rename failed", error);
      setApiNotice(`资料重命名接口调用失败：${errorMessage(error)}`);
    } finally {
      setRenamingMaterialId(null);
    }
  };

  const handleConvertPptMaterial = async (material: Material) => {
    if (!selectedSubject || deletingMaterialId || renamingMaterialId || convertingMaterialId) {
      return;
    }

    setConvertingMaterialId(material.id);
    setApiNotice("");
    try {
      const result = await convertPptMaterial(selectedSubject, material.relativePath);
      if (!result.success) {
        setApiNotice(result.warning || result.error || "PPT 转换失败，请检查后端日志。");
        return;
      }

      const [materialsResult, statusResult] = await Promise.allSettled([
        fetchSubjectMaterials(selectedSubject),
        fetchSubjectStatus(selectedSubject)
      ]);

      if (materialsResult.status === "fulfilled") {
        const nextMaterials = materialsResult.value;
        const nextMaterialIds = new Set(nextMaterials.map((item) => item.id));
        setMaterialItems(nextMaterials);
        setSelectedMaterialIds((current) => current.filter((id) => nextMaterialIds.has(id)));
      } else {
        console.error(
          `GET /api/subjects/${encodeURIComponent(selectedSubject)}/materials failed`,
          materialsResult.reason
        );
      }

      if (statusResult.status === "fulfilled") {
        setSubjectStatus(statusResult.value);
      } else {
        console.error(`GET /api/subjects/${encodeURIComponent(selectedSubject)}/status failed`, statusResult.reason);
      }

      setApiNotice("PPT 已转换，请添加/更新知识库。");
    } catch (error) {
      console.error("POST /materials/convert-ppt failed", error);
      setApiNotice(`PPT 转换接口调用失败：${errorMessage(error)}`);
    } finally {
      setConvertingMaterialId(null);
    }
  };

  const handleBuildIndex = async (mode: "update" | "reset") => {
    if (!selectedSubject || indexLoadingMode) {
      return;
    }
    if (mode === "update" && indexCorrupted) {
      setApiNotice(indexCorruptedHint);
      return;
    }

    setIndexLoadingMode(mode);
    try {
      const result = await buildIndex(selectedSubject, makeBuildPayload(mode));
      const actionText = mode === "update" ? "添加/更新" : "重建";
      if (result.success) {
        setQaWarning("");
        setQaErrorType(undefined);
        const [statusResult, materialsResult] = await Promise.allSettled([
          fetchSubjectStatus(selectedSubject),
          fetchSubjectMaterials(selectedSubject)
        ]);

        if (statusResult.status === "fulfilled") {
          setSubjectStatus(statusResult.value);
        } else {
          console.error(`GET /api/subjects/${encodeURIComponent(selectedSubject)}/status failed`, statusResult.reason);
        }

        if (materialsResult.status === "fulfilled") {
          const nextMaterials = materialsResult.value;
          const nextMaterialIds = new Set(nextMaterials.map((material) => material.id));
          setMaterialItems(nextMaterials);
          setSelectedMaterialIds((current) => current.filter((id) => nextMaterialIds.has(id)));
          if (statusResult.status === "fulfilled" && statusResult.value.index_status === "corrupted") {
            setApiNotice("重建后仍检测到索引异常，建议重启后端或再次执行重建。");
          } else if (statusResult.status === "fulfilled" && hasReadyIndex(statusResult.value)) {
            setQaWarning("");
            setQaErrorType(undefined);
            setApiNotice("知识库已更新，可直接提问。");
          } else {
            setApiNotice("知识库已更新，可直接提问。");
          }
        } else {
          console.error(
            `GET /api/subjects/${encodeURIComponent(selectedSubject)}/materials failed`,
            materialsResult.reason
          );
          setApiNotice("建库成功，但资料状态刷新失败，请手动刷新页面。");
        }
      } else {
        if (result.error_type === "index_corrupted") {
          setQaErrorType("index_corrupted");
          setQaWarning(result.warning || "当前索引已损坏，请使用重建当前范围知识库。");
        }
        setApiNotice(result.warning || result.error || `${actionText}知识库失败，请检查后端日志。`);
      }
    } catch (error) {
      console.error("POST /index failed", error);
      setApiNotice(`建库接口调用失败：${errorMessage(error)}`);
    } finally {
      setIndexLoadingMode(null);
    }
  };

  const handleOverview = async (forceRefresh: boolean) => {
    if (!selectedSubject || overviewLoading) {
      return;
    }
    if (subjectStatus?.index_status === "corrupted") {
      setOverviewWarning("当前科目的知识库索引可能已损坏，请先重建知识库。");
      return;
    }
    if (!subjectStatus || subjectStatus.index_status === "empty" || subjectStatus.indexed_count <= 0) {
      setOverviewWarning("当前科目还没有可用知识库，请先建立知识库。");
      return;
    }

    setOverviewLoading(true);
    setOverviewWarning("");
    try {
      const response = await fetchOverview(selectedSubject, {
        source_filters: selectedSourceFilters,
        use_deepseek: true,
        force_refresh: forceRefresh
      });
      setOverviewContent(response.content || "");
      setOverviewSources(response.sources || []);
      setOverviewReferences(response.references || []);
      setOverviewCached(response.cached);
      setOverviewWarning(response.warning || "");
      if (!response.success && response.warning) {
        setApiNotice(response.warning);
      }
    } catch (error) {
      console.error("POST /overview failed", error);
      setOverviewWarning(`资料概览接口调用失败：${errorMessage(error)}`);
    } finally {
      setOverviewLoading(false);
    }
  };

  const handleStudyGuide = async (forceRefresh: boolean) => {
    if (!selectedSubject || studyGuideLoading) {
      return;
    }
    if (subjectStatus?.index_status === "corrupted") {
      setStudyGuideWarning("当前科目的知识库索引可能已损坏，请先重建知识库。");
      return;
    }
    if (!subjectStatus || subjectStatus.index_status === "empty" || subjectStatus.indexed_count <= 0) {
      setStudyGuideWarning("当前科目还没有可用知识库，请先建立知识库。");
      return;
    }

    setStudyGuideLoading(true);
    setStudyGuideWarning("");
    try {
      const response = await fetchStudyGuide(selectedSubject, {
        source_filters: selectedSourceFilters,
        use_deepseek: true,
        force_refresh: forceRefresh
      });
      setStudyGuideContent(response.content || "");
      setStudyGuideSources(response.sources || []);
      setStudyGuideReferences(response.references || []);
      setStudyGuideCached(response.cached);
      setStudyGuideWarning(response.warning || "");
      if (!response.success && response.warning) {
        setApiNotice(response.warning);
      }
    } catch (error) {
      console.error("POST /study-guide failed", error);
      setStudyGuideWarning(`复习提纲接口调用失败：${errorMessage(error)}`);
    } finally {
      setStudyGuideLoading(false);
    }
  };

  const closeSourcePreview = () => {
    setPreviewHitIndex(null);
    setPreviewReferenceTarget(null);
    setPreviewReferenceList([]);
    setPreviewReferenceIndex(null);
    setImageError(false);
    setIsPreviewSnippetExpanded(false);
    setPreviewZoom("fit-width");
  };

  const handleSourceCardClick = (index: number) => {
    setPreviewNotice("");
    setImageError(false);
    setPreviewZoom("fit-width");
    setPreviewReferenceTarget(null);
    setPreviewReferenceList([]);
    setPreviewReferenceIndex(null);
    setPreviewHitIndex(index);
    setIsPreviewSnippetExpanded(false);
  };

  const loadHistory = async () => {
    if (!selectedSubject) return;
    setHistoryLoading(true);
    setHistoryError("");
    try {
      const data = await fetchQaHistory(selectedSubject);
      setHistoryItems(data.records || []);
    } catch (error) {
      setHistoryError("加载问答历史失败。");
      setHistoryItems([]);
    } finally {
      setHistoryLoading(false);
    }
  };

  const handleHistoryToggle = () => {
    const opening = !historyOpen;
    setHistoryOpen(opening);
    if (opening && historyItems.length === 0 && selectedSubject) {
      loadHistory();
    }
    setExpandedHistoryId(null);
  };

  const handleDeleteHistoryItem = async (id: number) => {
    if (deletingHistoryId !== null) return;
    if (!window.confirm("确定删除这条问答记录？")) return;

    setDeletingHistoryId(id);
    setHistoryError("");
    try {
      await deleteQaHistoryItem(id);
      setHistoryItems((items) => items.filter((item) => item.id !== id));
      if (expandedHistoryId === id) {
        setExpandedHistoryId(null);
      }
    } catch (error) {
      setHistoryError(`删除失败：${errorMessage(error)}`);
    } finally {
      setDeletingHistoryId(null);
    }
  };

  useEffect(() => {
    setExpandedHistoryId(null);
    setDeletingHistoryId(null);
    if (historyOpen && selectedSubject) {
      setHistoryItems([]);
      setHistoryError("");
      loadHistory();
    }
  }, [selectedSubject]);

  const handleAssistantSourceClick = (sourceNumber: number) => {
    const targetIndex = sourceNumber - 1;
    if (targetIndex < 0 || targetIndex >= qaHits.length) {
      return;
    }
    if (!sourcesExpanded) {
      setSourcesExpanded(true);
    }
    if (highlightTimerRef.current) {
      clearTimeout(highlightTimerRef.current);
    }
    window.setTimeout(() => {
      evidenceCardRefs.current[targetIndex]?.scrollIntoView({ behavior: "smooth", block: "center" });
    }, 50);
    setHighlightedEvidenceIndex(targetIndex);
    highlightTimerRef.current = setTimeout(() => {
      setHighlightedEvidenceIndex(null);
      highlightTimerRef.current = null;
    }, 1500);
  };

  const handleOverviewCitationClick = (sourceNumber: number) => {
    const targetIndex = sourceNumber - 1;
    if (targetIndex < 0 || targetIndex >= overviewSources.length) return;
    setOverviewSourcesExpanded(true);
    if (highlightTimerRef.current) clearTimeout(highlightTimerRef.current);
    window.setTimeout(() => {
      overviewCardRefs.current[targetIndex]?.scrollIntoView({ behavior: "smooth", block: "center" });
    }, 50);
    setHighlightedOverviewIndex(targetIndex);
    highlightTimerRef.current = setTimeout(() => {
      setHighlightedOverviewIndex(null);
      highlightTimerRef.current = null;
    }, 1500);
  };

  const handleStudyGuideCitationClick = (sourceNumber: number) => {
    const targetIndex = sourceNumber - 1;
    if (targetIndex < 0 || targetIndex >= studyGuideSources.length) return;
    setStudyGuideSourcesExpanded(true);
    if (highlightTimerRef.current) clearTimeout(highlightTimerRef.current);
    window.setTimeout(() => {
      studyGuideCardRefs.current[targetIndex]?.scrollIntoView({ behavior: "smooth", block: "center" });
    }, 50);
    setHighlightedStudyGuideIndex(targetIndex);
    highlightTimerRef.current = setTimeout(() => {
      setHighlightedStudyGuideIndex(null);
      highlightTimerRef.current = null;
    }, 1500);
  };

  const handleReferencePreviewClick = (
    preview: PreviewReferenceTarget,
    title: string,
    list: PreviewReferenceTarget[] = [preview],
    index = 0
  ) => {
    const safeList = list.length && index >= 0 ? list : [preview];
    const safeIndex = index >= 0 && index < safeList.length ? index : 0;
    setPreviewNotice("");
    setImageError(false);
    setPreviewZoom("fit-width");
    setPreviewHitIndex(null);
    setPreviewReferenceTarget({
      ...safeList[safeIndex],
      title
    });
    setPreviewReferenceList(safeList);
    setPreviewReferenceIndex(safeIndex);
    setIsPreviewSnippetExpanded(false);
  };

  const showPreviousPreviewHit = () => {
    setImageError(false);
    setIsPreviewSnippetExpanded(false);
    setPreviewHitIndex((index) => index === null ? index : Math.max(0, index - 1));
  };

  const showNextPreviewHit = () => {
    setImageError(false);
    setIsPreviewSnippetExpanded(false);
    setPreviewHitIndex((index) => index === null ? index : Math.min(qaHits.length - 1, index + 1));
  };

  const showPreviousReferencePreview = () => {
    if (previewReferenceIndex === null || previewReferenceList.length === 0) {
      return;
    }
    const nextIndex = Math.max(0, previewReferenceIndex - 1);
    setImageError(false);
    setIsPreviewSnippetExpanded(false);
    setPreviewReferenceIndex(nextIndex);
    setPreviewReferenceTarget({
      ...previewReferenceList[nextIndex],
      title: previewReferenceTarget?.title
    });
  };

  const showNextReferencePreview = () => {
    if (previewReferenceIndex === null || previewReferenceList.length === 0) {
      return;
    }
    const nextIndex = Math.min(previewReferenceList.length - 1, previewReferenceIndex + 1);
    setImageError(false);
    setIsPreviewSnippetExpanded(false);
    setPreviewReferenceIndex(nextIndex);
    setPreviewReferenceTarget({
      ...previewReferenceList[nextIndex],
      title: previewReferenceTarget?.title
    });
  };

  const handleAsk = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const trimmed = question.trim();
    if (!trimmed || !selectedSubject || qaLoading) {
      return;
    }

    const wasSelfTest = isSelfTestRequestRef.current;
    isSelfTestRequestRef.current = false;

    const displayQuestion = wasSelfTest ? "请基于当前资料生成自测题" : trimmed;
    const userMessage: ChatMessage = { role: "user", content: displayQuestion };
    setMessages([userMessage]);
    setQuestion("");
    setQaLoading(true);
    setQaWarning("");
    setQaErrorType(undefined);
    setExpandedEvidenceKeys({});
    setSourcesExpanded(false);
    setPreviewNotice("");
    closeSourcePreview();

    try {
      const response = await askQuestion(selectedSubject, {
        question: trimmed,
        source_filters: selectedMaterials.map((material) => material.relativePath),
        top_k: 8,
        use_deepseek: true
      });
      const answerText = response.answer || response.warning || "后端没有返回答案。";
      if (wasSelfTest) {
        setMessages([
          userMessage,
          { role: "assistant", content: "已生成自测题，见下方结果区。" }
        ]);
      } else {
        setMessages([
          userMessage,
          { role: "assistant", content: answerText }
        ]);
      }
      const hits = response.hits || [];
      setQaHits(hits);
      setQaWarning(response.warning || "");
      setQaErrorType(response.error_type);
      setApiNotice(response.warning ? `问答提示：${response.warning}` : "");

      if (wasSelfTest && answerText) {
        const scopeLabel = formatSourceScope(selectedMaterials.map((m) => m.relativePath));
        setSelfTestResult({
          subject: selectedSubject,
          scopeLabel,
          sourceFilters: selectedMaterials.map((m) => m.relativePath),
          generatedAt: new Date().toISOString(),
          content: answerText,
          hits,
          quizSettings: { ...quizSettings }
        });
        setActiveQuizTab("practice");
      }
    } catch (error) {
      console.error("POST /qa failed", error);
      setMessages([
        userMessage,
        {
          role: "assistant",
          content: `问答接口调用失败：${errorMessage(error)}\n\n请确认后端已启动、当前科目已建库，并稍后重试。`
        }
      ]);
      setQaHits([]);
      setQaWarning("问答接口调用失败，请稍后重试。");
      setQaErrorType(undefined);
      setExpandedEvidenceKeys({});
      setApiNotice(`POST /api/subjects/${encodeURIComponent(selectedSubject)}/qa 失败：${errorMessage(error)}`);
    } finally {
      setQaLoading(false);
    }
  };

  const handleQuickQuestionClick = (nextQuestion: string) => {
    if (qaLoading) {
      return;
    }
    setQuestion(nextQuestion);
    requestAnimationFrame(() => qaInputRef.current?.focus());
  };

  const handleSelfTestPromptClick = () => {
    if (qaLoading) {
      return;
    }
    setShowQuizModal(true);
    setActiveQuizTab("practice");
    setShowAnswer(false);
  };

  const handleStartQuiz = () => {
    const enabledCount = quizSettings.typeConfigs.filter((tc) => tc.enabled).length;
    if (enabledCount === 0) return;
    const prompt = buildSelfTestPrompt(quizSettings);
    isSelfTestRequestRef.current = true;
    setQuestion(prompt);
    setShowQuizModal(false);
    requestAnimationFrame(() => qaInputRef.current?.focus());
  };

  const handleCancelQuiz = () => {
    setShowQuizModal(false);
  };

  const handleQuizTypeToggle = (type: QuizQuestionType) => {
    setQuizSettings((prev) => {
      const configs = prev.typeConfigs.map((tc) => {
        if (tc.type !== type) return tc;
        const enabled = !tc.enabled;
        return { ...tc, enabled, count: enabled && tc.count === 0 ? 3 : tc.count };
      });
      return { ...prev, typeConfigs: configs };
    });
  };

  const handleQuizTypeCountAdj = (type: QuizQuestionType, delta: number) => {
    setQuizSettings((prev) => ({
      ...prev,
      typeConfigs: prev.typeConfigs.map((tc) => {
        if (tc.type !== type || !tc.enabled) return tc;
        const min = tc.enabled ? 1 : 0;
        return { ...tc, count: Math.max(min, Math.min(20, tc.count + delta)) };
      }),
    }));
  };

  const handleQuizTypeCountSet = (type: QuizQuestionType, count: number) => {
    setQuizSettings((prev) => ({
      ...prev,
      typeConfigs: prev.typeConfigs.map((tc) => {
        if (tc.type !== type) return tc;
        return { ...tc, count: Math.max(0, Math.min(20, count)) };
      }),
    }));
  };

  const handleQuizTypeMove = (type: QuizQuestionType, direction: "up" | "down") => {
    setQuizSettings((prev) => {
      const idx = prev.typeConfigs.findIndex((tc) => tc.type === type);
      if (idx < 0) return prev;
      const newIdx = direction === "up" ? idx - 1 : idx + 1;
      if (newIdx < 0 || newIdx >= prev.typeConfigs.length) return prev;
      const configs = [...prev.typeConfigs];
      [configs[idx], configs[newIdx]] = [configs[newIdx], configs[idx]];
      return { ...prev, typeConfigs: configs };
    });
  };

  const handleQuizTypeMoveIndex = (from: number, to: number) => {
    setQuizSettings((prev) => {
      const configs = [...prev.typeConfigs];
      const [removed] = configs.splice(from, 1);
      configs.splice(to, 0, removed);
      return { ...prev, typeConfigs: configs };
    });
  };

  const handleEyeToggle = () => {
    setShowAnswer((v) => {
      const next = !v;
      setActiveQuizTab(next ? "answer" : "practice");
      return next;
    });
  };

  const handleSelfTestSourceClick = (sourceNumber: number) => {
    if (!selfTestResult) return;
    const targetIndex = sourceNumber - 1;
    if (targetIndex < 0 || targetIndex >= selfTestResult.hits.length) return;
    selfTestSourceRefs.current[targetIndex]?.scrollIntoView({ behavior: "smooth", block: "center" });
    setHighlightedSelfTestSourceIndex(targetIndex);
    if (highlightTimerRef.current) clearTimeout(highlightTimerRef.current);
    highlightTimerRef.current = setTimeout(() => {
      setHighlightedSelfTestSourceIndex(null);
      highlightTimerRef.current = null;
    }, 1500);
  };

  const getQuizDisplayContent = (): string => {
    if (!selfTestResult) return "";
    const { quizSettings, content } = selfTestResult;
    if (quizSettings?.answerMode === "dual") {
      const practiceMatch = content.match(/## 练习版([\s\S]*?)(?=## 解析版|$)/);
      const answerMatch = content.match(/## 解析版([\s\S]*)/);
      if (activeQuizTab === "practice" && practiceMatch) return practiceMatch[1].trim();
      if (activeQuizTab === "answer" && answerMatch) return answerMatch[1].trim();
    }
    return content;
  };

  const handleSelfTestExportWord = async () => {
    if (!selfTestResult) return;
    const content = getQuizDisplayContent();
    const exportContent = selfTestIncludeSources ? content : stripCitationMarks(content);
    const citations = selfTestIncludeSources ? extractCitationNumbers(content) : undefined;
    const filename = buildExportFilename({
      subject: selfTestResult.subject,
      exportType: "自测题",
      scopeLabel: selfTestResult.scopeLabel,
      generatedAt: selfTestResult.generatedAt,
      extension: ".docx",
    });
    try {
      await exportDocumentDocx({
        title: "自测题",
        subject: selfTestResult.subject,
        scope_label: selfTestResult.scopeLabel,
        generated_at: selfTestResult.generatedAt,
        content: exportContent + (citations ? buildSourcesText(selfTestResult.hits, citations) : ""),
        sources: selfTestResult.hits,
        include_sources: selfTestIncludeSources,
        filename_prefix: filename,
        filename,
      });
    } catch (error) {
      setApiNotice(`Word 导出失败：${errorMessage(error)}`);
    }
  };

  const handlePrintPdf = () => {
    window.print();
  };

  const handleSelfTestPrint = () => {
    if (!selfTestResult) return;
    const content = getQuizDisplayContent();
    setPrintPayload({
      title: "自测题",
      subject: selfTestResult.subject,
      scopeLabel: selfTestResult.scopeLabel,
      generatedAt: selfTestResult.generatedAt,
      content: selfTestIncludeSources ? content : stripCitationMarks(content),
      sources: selfTestResult.hits,
      includeSources: selfTestIncludeSources,
      filename: buildExportFilename({
        subject: selfTestResult.subject,
        exportType: "自测题",
        scopeLabel: selfTestResult.scopeLabel,
        generatedAt: selfTestResult.generatedAt,
        extension: ".pdf",
      }),
    });
  };

  const handleLongformAnalysis = async () => {
    if (!selectedSubject || longformLoading) return;
    if (subjectStatus?.index_status === "corrupted") {
      setLongformError("当前科目的知识库索引可能已损坏，请先重建知识库。");
      return;
    }
    if (!subjectStatus || subjectStatus.index_status === "empty" || subjectStatus.indexed_count <= 0) {
      setLongformError("当前科目还没有可用知识库，请先建立知识库。");
      return;
    }

    const payload = {
      source_filters: selectedSourceFilters,
      longform_type: longformType,
      target_length: longformTargetLength,
      include_sources: longformIncludeSources,
      strategy: "staged" as const,
      user_instruction: longformInstruction,
    };

    setLongformLoading(true);
    setLongformError("");
    setLongformSettingsOpen(false);
    try {
      const response = await fetchLongformAnalysis(selectedSubject, payload);
      setLongformResult(response);
    } catch (error) {
      console.error("POST /longform failed", error);
      setLongformError(`资料整理接口调用失败：${errorMessage(error)}`);
    } finally {
      setLongformLoading(false);
    }
  };

  const handleLongformExportWord = async () => {
    if (!longformResult) return;
    const typeLabels: Record<string, string> = {
      analysis: "深度分析",
      study_notes: "学习笔记",
      report: "综合报告",
      review: "复习整理",
      outline: "知识框架",
    };
    const exportType = typeLabels[longformType] || "资料整理";
    const filename = buildExportFilename({
      subject: selectedSubject,
      exportType,
      scopeLabel: formatSourceScope(selectedMaterials.map((m) => m.relativePath)),
      generatedAt: new Date(),
      extension: ".docx",
    });
    try {
      await exportDocumentDocx({
        title: exportType,
        subject: selectedSubject,
        scope_label: formatSourceScope(selectedMaterials.map((m) => m.relativePath)),
        generated_at: new Date().toISOString(),
        content: longformResult.content,
        sources: longformResult.sources,
        include_sources: longformIncludeSources,
        filename_prefix: filename,
        filename,
      });
    } catch (error) {
      setApiNotice(`Word 导出失败：${errorMessage(error)}`);
    }
  };

  const handleLongformPrintPdf = () => {
    if (!longformResult) return;
    const typeLabels: Record<string, string> = {
      analysis: "深度分析",
      study_notes: "学习笔记",
      report: "综合报告",
      review: "复习整理",
      outline: "知识框架",
    };
    const exportType = typeLabels[longformType] || "资料整理";
    setPrintPayload({
      title: exportType,
      subject: selectedSubject,
      scopeLabel: formatSourceScope(selectedMaterials.map((m) => m.relativePath)),
      generatedAt: new Date().toISOString(),
      content: longformResult.content,
      sources: longformResult.sources,
      includeSources: longformIncludeSources,
      filename: buildExportFilename({
        subject: selectedSubject,
        exportType,
        scopeLabel: formatSourceScope(selectedMaterials.map((m) => m.relativePath)),
        generatedAt: new Date(),
        extension: ".pdf",
      }),
    });
  };

  const handleDocumentWord = async (payload: {
    title: string;
    subject: string;
    scopeLabel: string;
    generatedAt: string;
    content: string;
    sources: Hit[];
    includeSources: boolean;
    filename: string;
  }) => {
    try {
      const exportContent = payload.includeSources ? payload.content : stripCitationMarks(payload.content);
      const citations = payload.includeSources ? extractCitationNumbers(payload.content) : undefined;
      await exportDocumentDocx({
        title: payload.title,
        subject: payload.subject,
        scope_label: payload.scopeLabel,
        generated_at: payload.generatedAt,
        content: exportContent + (citations ? buildSourcesText(payload.sources, citations) : ""),
        sources: payload.sources,
        include_sources: payload.includeSources,
        filename_prefix: payload.filename,
        filename: payload.filename,
      });
    } catch (error) {
      setApiNotice(`Word 导出失败：${errorMessage(error)}`);
    }
  };

  const handleDocumentPrint = (payload: PrintPayload) => {
    setPrintPayload({
      ...payload,
      content: payload.includeSources ? payload.content : stripCitationMarks(payload.content),
    });
  };

  const getExportFilename = (exportType: string, extension: string) => {
    return buildExportFilename({
      subject: selectedSubject,
      exportType,
      scopeLabel: formatSourceScope(selectedMaterials.map((m) => m.relativePath)),
      generatedAt: new Date(),
      extension,
    });
  };

  const handleAiSettingsSave = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setAiSettingsSaving(true);
    setAiSettingsNotice("");

    try {
      const payload = {
        enabled: aiSettingsDraft.enabled,
        provider: aiSettingsDraft.provider,
        base_url: aiSettingsDraft.base_url.trim(),
        model: aiSettingsDraft.model.trim(),
        ...(aiSettingsDraft.api_key.trim() ? { api_key: aiSettingsDraft.api_key.trim() } : {})
      };
      const settings = await saveAiSettings(payload);
      setAiSettings(settings);
      setAiSettingsDraft({
        enabled: settings.enabled,
        provider: settings.provider,
        base_url: settings.base_url,
        model: settings.model,
        api_key: ""
      });
      setAiSettingsNotice("AI 设置已保存。");
      setShowAiSettingsModal(false);
    } catch (error) {
      setAiSettingsNotice(`AI 设置保存失败：${errorMessage(error)}`);
    } finally {
      setAiSettingsSaving(false);
    }
  };

  const isCustomDeepSeekModel = aiSettingsDraft.provider === "deepseek"
    && !deepseekModelOptions.some((option) => option.value === aiSettingsDraft.model);
  const showCustomDeepSeekModelInput = customModelExpanded || isCustomDeepSeekModel;
  const selectedAiProfile = getAiProviderProfile(aiSettings, aiSettingsDraft.provider);
  const subjectSelectDisabled = subjectsLoading || subjectCreateLoading || subjectOptions.length === 0;
  const selectedSubjectLabel = subjectsLoading
    ? "科目加载中..."
    : selectedSubject || "暂无科目";

  return (
    <main className={workspaceClassName} style={workspaceStyle}>
      <aside className="sidebar left-panel" style={sidebarStyle} aria-hidden={isSidebarCollapsed}>
        <section className="brand">
          <div className="brand-mark">学</div>
          <div className="brand-copy">
            <h1>课程资料智能学习助手</h1>
            <p>Course RAG Workspace</p>
          </div>
          <button
            className="sidebar-collapse-button"
            type="button"
            aria-label="折叠左侧栏"
            onClick={collapseSidebar}
          >
            ‹
          </button>
        </section>

        <section className="panel-block">
          <div className="section-heading">
            <span>科目选择</span>
            <span className="soft-badge">{subjectsLoading ? "加载中" : subjectOptions.length}</span>
          </div>
          <div
            className={`custom-subject-select ${isSubjectDropdownOpen ? "open" : ""}`}
            ref={subjectDropdownRef}
          >
            <button
              className="custom-subject-trigger"
              type="button"
              onClick={() => {
                if (!subjectSelectDisabled) {
                  setIsSubjectDropdownOpen((current) => !current);
                }
              }}
              disabled={subjectSelectDisabled}
              aria-haspopup="listbox"
              aria-expanded={isSubjectDropdownOpen}
            >
              <span className="custom-subject-label">{selectedSubjectLabel}</span>
              <span className="custom-subject-chevron" aria-hidden="true">⌄</span>
            </button>
            {isSubjectDropdownOpen && (
              <div className="custom-subject-menu" role="listbox" aria-label="科目选择">
                {subjectOptions.map((subject) => {
                  const isSelected = subject.name === selectedSubject;
                  return (
                    <button
                      className={`custom-subject-option ${isSelected ? "active" : ""}`}
                      key={subject.name}
                      type="button"
                      role="option"
                      aria-selected={isSelected}
                      onClick={() => {
                        setSelectedSubject(subject.name);
                        setIsSubjectDropdownOpen(false);
                      }}
                    >
                      <span>{subject.name}</span>
                    </button>
                  );
                })}
              </div>
            )}
          </div>
          {!isAddingSubject ? (
            <button
              className="add-subject-button"
              type="button"
              onClick={() => {
                setIsAddingSubject(true);
                setSubjectCreateError("");
              }}
              disabled={subjectCreateLoading}
            >
              + 添加科目
            </button>
          ) : (
            <form className="add-subject-form" onSubmit={handleCreateSubject}>
              <input
                type="text"
                value={newSubjectName}
                onChange={(event) => {
                  setNewSubjectName(event.target.value);
                  setSubjectCreateError("");
                }}
                placeholder="新科目名称"
                autoFocus
                disabled={subjectCreateLoading}
              />
              <div className="add-subject-actions">
                <button type="submit" disabled={subjectCreateLoading}>
                  {subjectCreateLoading ? "创建中..." : "确认"}
                </button>
                <button
                  type="button"
                  onClick={() => {
                    setIsAddingSubject(false);
                    setNewSubjectName("");
                    setSubjectCreateError("");
                  }}
                  disabled={subjectCreateLoading}
                >
                  取消
                </button>
              </div>
              {subjectCreateError && <div className="subject-create-error">{subjectCreateError}</div>}
            </form>
          )}
        </section>

        <section className="panel-block index-card">
          <div className="section-heading">
            <span>知识库操作</span>
            <span>{selectedMaterials.length ? "选中资料" : "全部资料"}</span>
          </div>
          <div className="index-actions">
            <button
              className="primary-build"
              type="button"
              onClick={() => handleBuildIndex("update")}
              disabled={!selectedSubject || indexLoadingMode !== null || indexCorrupted}
              title={indexCorrupted ? indexCorruptedHint : undefined}
            >
              {indexLoadingMode === "update" ? "正在建立知识库..." : "添加/更新当前范围"}
            </button>
            <button
              className={indexCorrupted ? "rebuild-highlight" : ""}
              type="button"
              onClick={() => handleBuildIndex("reset")}
              disabled={!selectedSubject || indexLoadingMode !== null}
            >
              {indexLoadingMode === "reset" ? "正在建立知识库..." : "重建当前范围"}
            </button>
          </div>
          {indexCorrupted && <div className="index-warning">{indexCorruptedHint}</div>}
        </section>

        <section className="panel-block material-manager">
          <div className="section-heading">
            <span>资料管理器</span>
            <span className="count-badge">
              {subjectDataLoading ? "加载中" : selectionCountText}
            </span>
          </div>
          <div className="range-tabs">
            <button
              className={materialSelectionMode === "materials" ? "active" : ""}
              type="button"
              onClick={() => switchMaterialSelectionMode("materials")}
            >
              多选资料
            </button>
            <button
              className={materialSelectionMode === "groups" ? "active" : ""}
              type="button"
              onClick={() => switchMaterialSelectionMode("groups")}
            >
              按章节
            </button>
          </div>
          <div className="explorer-content">
            {materialSelectionMode === "materials" ? (
              <div className="material-list">
                {materialItems.map((material) => {
                  const selected = selectedMaterialIds.includes(material.id);
                  const deleting = deletingMaterialId === material.id;
                  const renaming = renamingMaterialId === material.id;
                  const converting = convertingMaterialId === material.id;
                  const isPptMaterial = (material.fileExtension || `.${material.fileType.toLowerCase()}`) === ".ppt";
                  const materialActionBusy =
                    deletingMaterialId !== null || renamingMaterialId !== null || convertingMaterialId !== null;
                  return (
                    <article
                      className={`material-row ${selected ? "selected" : ""}`}
                      key={material.id}
                      onClick={() => toggleMaterial(material.id)}
                      role="button"
                      tabIndex={0}
                      onKeyDown={(event) => {
                        if ((event.target as HTMLElement).closest("button")) {
                          return;
                        }
                        if (event.key === "Enter" || event.key === " ") {
                          event.preventDefault();
                          toggleMaterial(material.id);
                        }
                      }}
                    >
                      <span className={`check-dot ${selected ? "selected" : ""}`} aria-hidden="true" />
                      <span className="material-main">
                        <span className="material-name" title={material.fileName}>{material.fileName}</span>
                      </span>
                      <span className="material-actions">
                        <span className="material-meta">
                          {material.fileType} · {material.sizeLabel} · {material.category}
                        </span>
                        <span className={`status-pill ${statusClass[material.status]}`}>{material.status}</span>
                        <span className="action-buttons">
                          {isPptMaterial && (
                            <button
                              className="convert-material"
                              type="button"
                              title={converting ? "转换中" : "转换"}
                              aria-label={converting ? "转换中" : "转换"}
                              onClick={(event) => {
                                event.stopPropagation();
                                handleConvertPptMaterial(material);
                              }}
                              disabled={materialActionBusy}
                            >
                              ↻
                            </button>
                          )}
                          <button
                            className="rename-material"
                            type="button"
                            title={renaming ? "重命名中" : "重命名"}
                            aria-label={renaming ? "重命名中" : "重命名"}
                            onClick={(event) => {
                              event.stopPropagation();
                              handleRenameMaterial(material);
                            }}
                            disabled={materialActionBusy}
                          >
                            ✎
                          </button>
                          <button
                            className="remove-material"
                            type="button"
                            title={deleting ? "移除中" : "移除"}
                            aria-label={deleting ? "移除中" : "移除"}
                            onClick={(event) => {
                              event.stopPropagation();
                              handleDeleteMaterial(material);
                            }}
                            disabled={materialActionBusy}
                          >
                            ×
                          </button>
                        </span>
                      </span>
                    </article>
                  );
                })}
                {!subjectDataLoading && materialItems.length === 0 && (
                  <div className="empty-state">当前科目还没有资料。</div>
                )}
              </div>
            ) : (
              <div className="chapter-group-list">
                {materialGroups.map((group) => {
                  const selected = selectedGroupNames.includes(group.name);
                  const expanded = expandedGroupNames.includes(group.name);
                  return (
                    <article
                      className={`chapter-group-card ${selected ? "selected" : ""} ${expanded ? "expanded" : ""}`}
                      key={group.name}
                    >
                      <div className="chapter-group-row">
                        <button
                          className="chapter-group-toggle"
                          type="button"
                          aria-expanded={expanded}
                          onClick={() => toggleMaterialGroupExpanded(group.name)}
                        >
                          <span className="chapter-arrow" aria-hidden="true">{expanded ? "⌄" : "›"}</span>
                          <span className="chapter-group-title">
                            <strong>{group.name}</strong>
                            <small>{group.materials.length} 份资料</small>
                          </span>
                        </button>
                        <button
                          className={`group-check ${selected ? "selected" : ""}`}
                          type="button"
                          aria-label={`${selected ? "取消选择" : "选择"}${group.name}`}
                          aria-pressed={selected}
                          onClick={(event) => {
                            event.stopPropagation();
                            toggleMaterialGroup(group.name);
                          }}
                        />
                      </div>
                      {expanded && (
                        <div className="chapter-materials-list" onClick={(event) => event.stopPropagation()}>
                          {group.materials.map((material) => (
                            <article className="chapter-material-row" key={material.id}>
                              <span className="chapter-material-name" title={material.fileName}>
                                {material.fileName}
                              </span>
                              <span className="chapter-material-meta">
                                <span>
                                  {material.fileType} · {material.sizeLabel} · {material.category}
                                </span>
                                <span className={`status-pill ${statusClass[material.status]}`}>{material.status}</span>
                              </span>
                            </article>
                          ))}
                        </div>
                      )}
                    </article>
                  );
                })}
                {!materialGroups.length && <div className="empty-state">当前科目还没有可分组资料。</div>}
              </div>
            )}
          </div>
        </section>

        <section className={`panel-block upload-card ${uploadFiles.length || uploadLoading ? "upload-ready" : "upload-idle"}`}>
          <div className="section-heading">
            <span>上传资料</span>
            <span>{uploadFiles.length ? `${uploadFiles.length} 个文件` : "未选择"}</span>
          </div>
          <input
            id="material-upload-input"
            className="upload-input"
            type="file"
            multiple
            accept=".ppt,.pptx,.pdf,.docx,.txt"
            onChange={handleUploadFileChange}
            disabled={uploadLoading}
          />
          <label
            className={`upload-picker ${uploadLoading ? "disabled" : ""}`}
            htmlFor="material-upload-input"
          >
            <span className="upload-picker-icon">+</span>
            <span>{uploadFiles.length ? `已选择 ${uploadFiles.length} 个文件` : "点击选择资料"}</span>
          </label>
          <button type="button" onClick={handleUploadMaterials} disabled={uploadLoading || uploadFiles.length === 0}>
            {uploadLoading ? "上传中..." : "上传资料"}
          </button>
          {uploadNotice && <div className="upload-notice">{uploadNotice}</div>}
        </section>
      </aside>

      {!isSidebarCollapsed && (
        <div
          className="sidebar-resizer"
          role="separator"
          aria-label="调整左侧栏宽度"
          aria-orientation="vertical"
          aria-valuemin={SIDEBAR_WIDTH_MIN}
          aria-valuemax={SIDEBAR_WIDTH_MAX}
          aria-valuenow={sidebarWidth}
          onPointerDown={handleSidebarResizeStart}
        />
      )}

      <section className="center-panel">
        {isSidebarCollapsed && (
          <button
            className="sidebar-expand-button"
            type="button"
            aria-label="展开左侧栏"
            onClick={expandSidebar}
          >
            展开侧栏
          </button>
        )}
        {apiNotice && <div className="notice-banner">{apiNotice}</div>}
        <header className="top-status" aria-label="当前工作区状态">
          <strong className="status-subject">{selectedSubject || "加载中"}</strong>
          <span className="status-divider">·</span>
          <span>{subjectDataLoading ? "资料加载中" : `${subjectStatus?.file_count ?? materialItems.length} 份资料`}</span>
          <span className="status-divider">·</span>
          <span>{subjectDataLoading ? "chunks 加载中" : `${subjectStatus?.indexed_count ?? 0} chunks`}</span>
          <span className="status-divider">·</span>
          <button
            className={`ai-status-chip ${aiSettings.enabled && aiSettings.has_api_key ? "online" : "offline"}`}
            type="button"
            onClick={() => {
              setCustomModelExpanded(false);
              setShowAiSettingsModal(true);
            }}
          >
            <span className="deepseek-dot" aria-hidden="true" />
            {aiSettingsLoading
              ? "AI 加载中"
              : aiSettings.enabled
                ? aiSettings.has_api_key
                  ? `AI 已配置（${getAiModelModeLabel(aiSettings.model)}）`
                  : "AI 缺少 Key"
                : "AI 已关闭"}
          </button>
        </header>

        <article className="card insights-card">
          <div className="insight-header">
            <div>
              <div className="insight-title">资料洞察</div>
              <div className="study-guide-scope">
                <strong>当前范围：{currentScopeText}</strong>
                {activeInsightTab === "overview" && overviewCached && <span>缓存</span>}
                {activeInsightTab === "guide" && studyGuideCached && <span>缓存</span>}
              </div>
            </div>
            <div className="insight-actions">
              <div className="insight-tabs" role="tablist" aria-label="资料洞察类型">
                <button
                  className={activeInsightTab === "overview" ? "active" : ""}
                  type="button"
                  role="tab"
                  aria-selected={activeInsightTab === "overview"}
                  onClick={() => setActiveInsightTab("overview")}
                >
                  资料概览
                  <span className="mode-badge mode-badge-fast">快速模式</span>
                </button>
                <button
                  className={activeInsightTab === "guide" ? "active" : ""}
                  type="button"
                  role="tab"
                  aria-selected={activeInsightTab === "guide"}
                  onClick={() => setActiveInsightTab("guide")}
                >
                  复习提纲
                  <span className="mode-badge mode-badge-fast">快速模式</span>
                </button>
              </div>
              {((activeInsightTab === "overview" && overviewContent) || (activeInsightTab === "guide" && studyGuideContent)) && (
                <div className="insight-export-actions">
                  <label className="export-inline-label">
                    <input
                      type="checkbox"
                      checked={activeInsightTab === "overview" ? overviewIncludeSources : studyGuideIncludeSources}
                      onChange={() => {
                        if (activeInsightTab === "overview") setOverviewIncludeSources((v) => !v);
                        else setStudyGuideIncludeSources((v) => !v);
                      }}
                    />
                    来源
                  </label>
                  <button type="button" className="insight-export-btn" onClick={async () => {
                    if (activeInsightTab === "overview") {
                      await handleDocumentWord({
                        title: "资料概览",
                        subject: selectedSubject,
                        scopeLabel: formatSourceScope(selectedMaterials.map((m) => m.relativePath)),
                        generatedAt: new Date().toISOString(),
                        content: overviewContent,
                        sources: (overviewSources as unknown as Hit[]),
                        includeSources: overviewIncludeSources,
                        filename: getExportFilename("资料概览", ".docx"),
                      });
                    } else {
                      await handleDocumentWord({
                        title: "复习提纲",
                        subject: selectedSubject,
                        scopeLabel: formatSourceScope(selectedMaterials.map((m) => m.relativePath)),
                        generatedAt: new Date().toISOString(),
                        content: studyGuideContent,
                        sources: (studyGuideSources as unknown as Hit[]),
                        includeSources: studyGuideIncludeSources,
                        filename: getExportFilename("复习提纲", ".docx"),
                      });
                    }
                  }}>Word</button>
                  <button type="button" className="insight-export-btn" onClick={() => {
                    if (activeInsightTab === "overview") {
                      handleDocumentPrint({
                        title: "资料概览",
                        subject: selectedSubject,
                        scopeLabel: formatSourceScope(selectedMaterials.map((m) => m.relativePath)),
                        generatedAt: new Date().toISOString(),
                        content: overviewContent,
                        sources: (overviewSources as unknown as Hit[]),
                        includeSources: overviewIncludeSources,
                        filename: getExportFilename("资料概览", ".pdf"),
                      });
                    } else {
                      handleDocumentPrint({
                        title: "复习提纲",
                        subject: selectedSubject,
                        scopeLabel: formatSourceScope(selectedMaterials.map((m) => m.relativePath)),
                        generatedAt: new Date().toISOString(),
                        content: studyGuideContent,
                        sources: (studyGuideSources as unknown as Hit[]),
                        includeSources: studyGuideIncludeSources,
                        filename: getExportFilename("复习提纲", ".pdf"),
                      });
                    }
                  }}>PDF</button>
                </div>
              )}
              {activeInsightTab === "overview" ? (
                <button
                  type="button"
                  onClick={() => handleOverview(Boolean(overviewContent))}
                  disabled={overviewLoading}
                >
                  {overviewLoading
                    ? "生成中..."
                    : overviewContent
                      ? "重新生成"
                      : "生成概览"}
                </button>
              ) : (
                <button
                  type="button"
                  onClick={() => handleStudyGuide(Boolean(studyGuideContent))}
                  disabled={studyGuideLoading}
                >
                  {studyGuideLoading
                    ? "生成中..."
                    : studyGuideContent
                      ? "重新生成"
                      : "生成提纲"}
                </button>
              )}
            </div>
          </div>
          {!aiSettings.enabled && <div className="ai-disabled-hint">{aiDisabledHint}</div>}

          <div className="insight-body">
            {activeInsightTab === "overview" ? (
              <>
                {overviewWarning && <div className="inline-warning">{overviewWarning}</div>}
                {overviewContent ? (
                  <>
                    <div className="markdown-content">{renderRichMarkdown(overviewContent, overviewSources.length, handleOverviewCitationClick)}</div>
                  </>
                ) : (
                  <div className="empty-state overview-empty">
                    <span className="floating-welcome-icon" aria-hidden="true">✨</span>
                    根据当前资料范围生成资料概览
                  </div>
                )}
                {overviewReferencePreviews.length > 0 && (
                  <section className="related-pages">
                    <div className="evidence-header">
                      <strong>概览引用页面</strong>
                    </div>
                    <div className="related-page-grid">
                      {overviewReferencePreviews.map(({ key, imageUrl, sourcePath, fileName, fileType, pageNumber, snippet }, index) => (
                        <button
                          className="related-page-card"
                          type="button"
                          key={key}
                          onClick={() => handleReferencePreviewClick({
                            sourcePath,
                            fileName,
                            fileType,
                            pageNumber,
                            snippet
                          }, "概览引用页面", overviewReferencePreviews, index)}
                        >
                          <PagePreviewThumb
                            imageUrl={imageUrl}
                            alt={`${fileName} ${
                              fileType === "pdf"
                                ? `第 ${Math.trunc(pageNumber)} 页`
                                : `第 ${Math.trunc(pageNumber)} 张幻灯片`
                            }`}
                          />
                          <span className="related-page-meta">
                            <strong>{fileName}</strong>
                            <span>
                              {fileType === "pdf"
                                ? `第 ${Math.trunc(pageNumber)} 页`
                                : `第 ${Math.trunc(pageNumber)} 张幻灯片`}
                              {" · "}
                              {fileType.toUpperCase()}
                            </span>
                            <span className="related-page-hint">
                              {getReferenceHint("这页主要支撑：", snippet)}
                            </span>
                          </span>
                        </button>
                      ))}
                    </div>
                  </section>
                )}
                {overviewSources.length > 0 && (
                  <div className="study-guide-sources">
                    <button type="button" className="source-collapse-toggle" onClick={() => setOverviewSourcesExpanded((value) => !value)}>
                      {overviewSourcesExpanded ? "收起" : "展开"}来源资料（{overviewSources.length}）
                    </button>
                    {overviewSourcesExpanded && (
                    <div className="source-collapse-list">
                      {overviewSources.map((source, index) => {
                        const previewTarget = sourceToPreviewTarget(source);
                        const previewIndex = previewTarget
                          ? overviewSourcePreviewTargets.findIndex((target) => samePreviewTarget(target, previewTarget))
                          : -1;
                        const sourceKey = `${source.rank || index}-${source.label || source.source_path || index}`;
                        const sourceContent = (
                          <>
                            <span>{source.rank ? `[${source.rank}] ` : ""}{source.label || source.source_path || "来源"}</span>
                            <p className="study-guide-source-hint">{getReferenceHint("概览依据：", source.text)}</p>
                          </>
                        );

                        return (
                          <article
                            className={`${previewTarget ? "study-guide-source-action" : ""}${highlightedOverviewIndex === index ? " source-card-highlight" : ""}`}
                            key={sourceKey}
                            ref={(node) => { overviewCardRefs.current[index] = node; }}
                            role={previewTarget ? "button" : undefined}
                            tabIndex={previewTarget ? 0 : undefined}
                            onClick={previewTarget ? () => handleReferencePreviewClick(
                              previewTarget,
                              "概览来源资料",
                              overviewSourcePreviewTargets,
                              previewIndex
                            ) : undefined}
                            onKeyDown={previewTarget ? (event) => {
                              if (event.key === "Enter" || event.key === " ") {
                                event.preventDefault();
                                handleReferencePreviewClick(
                                  previewTarget,
                                  "概览来源资料",
                                  overviewSourcePreviewTargets,
                                  previewIndex
                                );
                              }
                            } : undefined}
                          >
                            {sourceContent}
                          </article>
                        );
                      })}
                    </div>
                    )}
                  </div>
                )}
              </>
            ) : (
              <>
                {studyGuideWarning && <div className="inline-warning">{studyGuideWarning}</div>}
                {studyGuideContent ? (
                  <>
                    <div className="markdown-content">{renderRichMarkdown(studyGuideContent, studyGuideSources.length, handleStudyGuideCitationClick)}</div>
                  </>
                ) : (
                  <div className="empty-state outline-empty">
                    <span className="floating-welcome-icon" aria-hidden="true">📝</span>
                    根据当前资料范围生成复习提纲
                  </div>
                )}
                {studyGuideReferencePreviews.length > 0 && (
                  <section className="related-pages">
                    <div className="evidence-header">
                      <strong>提纲引用页面</strong>
                    </div>
                    <div className="related-page-grid">
                      {studyGuideReferencePreviews.map(({ key, imageUrl, sourcePath, fileName, fileType, pageNumber, snippet }, index) => (
                        <button
                          className="related-page-card"
                          type="button"
                          key={key}
                          onClick={() => handleReferencePreviewClick({
                            sourcePath,
                            fileName,
                            fileType,
                            pageNumber,
                            snippet
                          }, "提纲引用页面", studyGuideReferencePreviews, index)}
                        >
                          <PagePreviewThumb
                            imageUrl={imageUrl}
                            alt={`${fileName} ${
                              fileType === "pdf"
                                ? `第 ${Math.trunc(pageNumber)} 页`
                                : `第 ${Math.trunc(pageNumber)} 张幻灯片`
                            }`}
                          />
                          <span className="related-page-meta">
                            <strong>{fileName}</strong>
                            <span>
                              {fileType === "pdf"
                                ? `第 ${Math.trunc(pageNumber)} 页`
                                : `第 ${Math.trunc(pageNumber)} 张幻灯片`}
                              {" · "}
                              {fileType.toUpperCase()}
                            </span>
                            <span className="related-page-hint">
                              {getReferenceHint("复习时看这里：", snippet)}
                            </span>
                          </span>
                        </button>
                      ))}
                    </div>
                  </section>
                )}
                {studyGuideSources.length > 0 && (
                  <div className="study-guide-sources">
                    <button type="button" className="source-collapse-toggle" onClick={() => setStudyGuideSourcesExpanded((value) => !value)}>
                      {studyGuideSourcesExpanded ? "收起" : "展开"}来源资料（{studyGuideSources.length}）
                    </button>
                    {studyGuideSourcesExpanded && (
                    <div className="source-collapse-list">
                      {studyGuideSources.map((source, index) => {
                        const previewTarget = sourceToPreviewTarget(source);
                        const previewIndex = previewTarget
                          ? studyGuideSourcePreviewTargets.findIndex((target) => samePreviewTarget(target, previewTarget))
                          : -1;
                        const sourceKey = `${source.rank || index}-${source.label || source.source_path || index}`;
                        const sourceContent = (
                          <>
                            <span>{source.rank ? `[${source.rank}] ` : ""}{source.label || source.source_path || "来源"}</span>
                            <p className="study-guide-source-hint">{getReferenceHint("提纲依据：", source.text)}</p>
                          </>
                        );

                        return (
                          <article
                            className={`${previewTarget ? "study-guide-source-action" : ""}${highlightedStudyGuideIndex === index ? " source-card-highlight" : ""}`}
                            key={sourceKey}
                            ref={(node) => { studyGuideCardRefs.current[index] = node; }}
                            role={previewTarget ? "button" : undefined}
                            tabIndex={previewTarget ? 0 : undefined}
                            onClick={previewTarget ? () => handleReferencePreviewClick(
                              previewTarget,
                              "提纲来源资料",
                              studyGuideSourcePreviewTargets,
                              previewIndex
                            ) : undefined}
                            onKeyDown={previewTarget ? (event) => {
                              if (event.key === "Enter" || event.key === " ") {
                                event.preventDefault();
                                handleReferencePreviewClick(
                                  previewTarget,
                                  "提纲来源资料",
                                  studyGuideSourcePreviewTargets,
                                  previewIndex
                                );
                              }
                            } : undefined}
                          >
                            {sourceContent}
                          </article>
                        );
                      })}
                    </div>
                    )}
                  </div>
                )}
              </>
            )}
          </div>
          <div
            className="insight-resizer"
            role="separator"
            aria-label="调整资料洞察高度"
            aria-orientation="horizontal"
            aria-valuemin={INSIGHT_HEIGHT_MIN}
            aria-valuemax={INSIGHT_HEIGHT_MAX}
            aria-valuenow={insightHeight}
            onPointerDown={handleInsightResizeStart}
          />
        </article>

        <section className="card qa-card">
          <div className="card-title">
            <span>智能问答</span>
            <span className="soft-badge">{qaLoading ? "定位依据中" : "🛡️ 知识库受控问答"}</span>
            <button
              className="qa-history-toggle"
              type="button"
              onClick={handleHistoryToggle}
              disabled={!selectedSubject}
            >
              历史
            </button>
          </div>
          {historyOpen && (
            <div className="qa-history-panel">
              <div className="qa-history-header">
                <span className="qa-history-header-subject">{selectedSubject}</span>
                <span className="qa-history-header-count">{historyItems.length} 条记录</span>
              </div>
              {historyLoading && <div className="qa-history-loading">加载中…</div>}
              {historyError && <div className="qa-history-error">{historyError}</div>}
              {!historyLoading && !historyError && historyItems.length === 0 && (
                <div className="qa-history-empty">当前科目暂无问答历史。</div>
              )}
              {!historyLoading && !historyError && historyItems.length > 0 && (
                <div className="qa-history-list">
                  {historyItems.map((item) => (
                    <div
                      className={`qa-history-item${expandedHistoryId === item.id ? " active" : ""}`}
                      key={item.id}
                    >
                      <button
                        className="qa-history-item-head"
                        type="button"
                        onClick={() =>
                          setExpandedHistoryId(expandedHistoryId === item.id ? null : item.id)
                        }
                      >
                        <span className="qa-history-question">{item.question}</span>
                        <span className="qa-history-meta">
                          <span className="qa-history-tag">{item.subject}</span>
                          <span>{formatTime(item.created_at)}</span>
                          {item.answer_mode && <span>{item.answer_mode}</span>}
                          {item.hits_count > 0 && <span>{item.hits_count} 条依据</span>}
                          <span className="qa-history-scope">{formatSourceScope(item.source_filters)}</span>
                          <button
                            className="qa-history-delete"
                            type="button"
                            disabled={deletingHistoryId !== null}
                            onClick={(event) => {
                              event.stopPropagation();
                              handleDeleteHistoryItem(item.id);
                            }}
                            aria-label="删除此条记录"
                          >
                            {deletingHistoryId === item.id ? "删除中" : "删除"}
                          </button>
                        </span>
                      </button>
                      {expandedHistoryId === item.id && (
                        <div className="qa-history-detail">
                          {item.source_filters && item.source_filters.length > 0 && (
                            <div className="qa-history-scope-detail">
                              <span className="qa-history-scope-title">资料范围</span>
                              <ul className="qa-history-scope-list">
                                {item.source_filters.map((path, i) => (
                                  <li key={i}>{path}</li>
                                ))}
                              </ul>
                            </div>
                          )}
                          <div className="qa-history-answer">{item.answer}</div>
                          {item.hits && item.hits.length > 0 ? (
                            <details className="qa-history-sources-details">
                              <summary className="qa-history-sources-summary">
                                查看历史来源依据（{item.hits.length}）
                              </summary>
                              <div className="qa-history-hits">
                                {(() => {
                                  const previewList = item.hits.map(hitToPreviewTarget).filter(Boolean) as PreviewReferenceTarget[];
                                  return item.hits.map((hit, i) => {
                                    const target = previewList[i];
                                    if (!target) return null;
                                    return (
                                      <article
                                        className="evidence-card evidence-card-action"
                                        key={i}
                                        role="button"
                                        tabIndex={0}
                                        onClick={() => handleReferencePreviewClick(target, "历史来源依据", previewList, i)}
                                        onKeyDown={(event) => {
                                          if (event.key === "Enter" || event.key === " ") {
                                            event.preventDefault();
                                            handleReferencePreviewClick(target, "历史来源依据", previewList, i);
                                          }
                                        }}
                                      >
                                        <div className="evidence-card-head">
                                          <span className="evidence-rank">#{i + 1}</span>
                                          <strong>{target.fileName}</strong>
                                          <span className="evidence-location">
                                            {target.fileType === "pdf"
                                              ? `第 ${Math.trunc(target.pageNumber)} 页`
                                              : `第 ${Math.trunc(target.pageNumber)} 张幻灯片`}
                                          </span>
                                        </div>
                                        <div className="evidence-snippet">
                                          <p>{shortenText(target.snippet || "")}</p>
                                        </div>
                                      </article>
                                    );
                                  });
                                })()}
                              </div>
                            </details>
                          ) : (
                            <div className="qa-history-hits-empty">此历史记录未保存来源依据。</div>
                          )}
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}
          <div className="qa-scope-capsule" aria-label="当前受控知识库范围">
            <span>{qaScopeCapsuleText}</span>
          </div>
          {!aiSettings.enabled && <div className="ai-disabled-hint">{aiDisabledHint}</div>}
          <div className="chat-window">
            {messages.length === 0 && (
              <div className="qa-empty-state">
                <span className="qa-empty-icon floating-welcome-icon" aria-hidden="true">✨</span>
                <strong>选择 1 到 3 个章节资料提问，答案会更聚焦，也更容易核对来源。</strong>
                <div className="qa-empty-prompts">
                  {quickQuestions.map((quickQuestion) => (
                    <button
                      type="button"
                      key={quickQuestion}
                      onClick={() => handleQuickQuestionClick(quickQuestion)}
                      disabled={qaLoading}
                    >
                      {quickQuestion}
                    </button>
                  ))}
                </div>
              </div>
            )}
            {messages.map((message, index) => (
              <div className={`message ${message.role}`} key={`${message.role}-${index}`}>
                <span className="message-avatar" aria-hidden="true">{message.role === "user" ? "○" : "✦"}</span>
                {message.role === "assistant" ? (
                  <div className="message-content">{renderAssistantContent(message.content, qaHits.length, handleAssistantSourceClick)}</div>
                ) : (
                  <p className="message-bubble">{message.content}</p>
                )}
              </div>
            ))}
            {qaLoading && (
              <div className="qa-loading-state" aria-live="polite">
                <span aria-hidden="true" />
                {qaLoadingText}
              </div>
            )}
            {previewNotice && <div className="source-preview-notice">{previewNotice}</div>}
            {!selfTestResult && relatedPreviewHits.length > 0 && (
              <section className="related-pages">
                <div className="evidence-header">
                  <strong>相关页面图</strong>
                </div>
                <div className="related-page-grid">
                  {relatedPreviewHits.map(({ index, imageUrl, fileName, fileType, pageNumber }) => (
                    <button
                      className="related-page-card"
                      type="button"
                      key={`${fileName}-${index}`}
                      onClick={() => {
                        setPreviewNotice("");
                        setImageError(false);
                        setPreviewReferenceTarget(null);
                        setPreviewReferenceList([]);
                        setPreviewReferenceIndex(null);
                        setPreviewHitIndex(index);
                      }}
                    >
                      <PagePreviewThumb
                        imageUrl={imageUrl}
                        alt={`${fileName} ${
                          fileType === "pdf"
                            ? `第 ${Math.trunc(pageNumber)} 页`
                            : `第 ${Math.trunc(pageNumber)} 张幻灯片`
                        }`}
                      />
                      <span className="related-page-meta">
                        <strong>{fileName}</strong>
                        <span>
                          {fileType === "pdf"
                            ? `第 ${Math.trunc(pageNumber)} 页`
                            : `第 ${Math.trunc(pageNumber)} 张幻灯片`}
                          {" · "}
                          {fileType.toUpperCase()}
                        </span>
                      </span>
                    </button>
                  ))}
                </div>
              </section>
            )}
            {!selfTestResult && sourceItems.length > 0 && (
              <section className="evidence-ledger">
                <div className="evidence-header">
                  <strong>{evidenceTitle}</strong>
                  <button type="button" className="evidence-ledger-toggle" onClick={() => setSourcesExpanded((value) => !value)}>
                    {sourcesExpanded ? "收起来源" : "展开来源"}
                  </button>
                </div>
                {sourcesExpanded && (
                <div className="evidence-list">
                  {evidenceItems.map(({ hit, index, source }) => {
                    const level = getEvidenceLevel(index);
                    const evidenceKey = String(index);
                    const rawEvidenceText = hit.text || "暂无片段摘要。";
                    const compactEvidenceText = rawEvidenceText.replace(/\s+/g, " ").trim();
                    const evidenceExpanded = Boolean(expandedEvidenceKeys[evidenceKey]);
                    const evidenceText = evidenceExpanded
                      ? rawEvidenceText
                      : shortenText(rawEvidenceText, EVIDENCE_SUMMARY_LENGTH);
                    const canExpandEvidence = compactEvidenceText.length > EVIDENCE_SUMMARY_LENGTH;
                    return (
                      <article
                        className={`evidence-card evidence-card-action${highlightedEvidenceIndex === index ? " evidence-card-highlight" : ""}`}
                        key={source.id}
                        ref={(node) => { evidenceCardRefs.current[index] = node; }}
                        role="button"
                        tabIndex={0}
                        onClick={() => handleSourceCardClick(index)}
                        onKeyDown={(event) => {
                          if (event.key === "Enter" || event.key === " ") {
                            event.preventDefault();
                            handleSourceCardClick(index);
                          }
                        }}
                      >
                        <div className="evidence-card-head">
                          <span className="evidence-rank">#{index + 1}</span>
                          <strong>{source.fileName}</strong>
                          <span className="evidence-location">{source.location}</span>
                          <span className={`evidence-level ${level.className}`}>{level.text}</span>
                        </div>
                        <div className={`evidence-snippet ${evidenceExpanded ? "expanded" : ""}`}>
                          <p>{evidenceText}</p>
                          {canExpandEvidence && (
                            <button
                              className="evidence-expand-button"
                              type="button"
                              onClick={(event) => {
                                event.stopPropagation();
                                setExpandedEvidenceKeys((current) => ({
                                  ...current,
                                  [evidenceKey]: !current[evidenceKey]
                                }));
                              }}
                            >
                              {evidenceExpanded ? "收起原文" : "展开原文"}
                            </button>
                          )}
                        </div>
                      </article>
                    );
                  })}
                </div>
                )}
              </section>
            )}
          </div>
          {!selfTestResult && messages.filter((m) => m.role === "assistant" && m.content !== "已生成自测题，见下方结果区。").length > 0 && (
            <div className="export-toolbar">
              <label className="export-include-label">
                <input
                  type="checkbox"
                  checked={qaIncludeSources}
                  onChange={() => setQaIncludeSources((v) => !v)}
                />
                附带来源
              </label>
              <button type="button" className="export-word-btn" onClick={async () => {
                const lastMsg = [...messages].reverse().find((m) => m.role === "assistant" && m.content !== "已生成自测题，见下方结果区。");
                if (!lastMsg) return;
                await handleDocumentWord({
                  title: "智能问答回答",
                  subject: selectedSubject,
                  scopeLabel: formatSourceScope(selectedMaterials.map((m) => m.relativePath)),
                  generatedAt: new Date().toISOString(),
                  content: lastMsg.content,
                  sources: qaHits,
                  includeSources: qaIncludeSources,
                  filename: getExportFilename("智能问答", ".docx"),
                });
              }}>
                导出 Word
              </button>
              <button type="button" className="export-pdf-btn" onClick={() => {
                const lastMsg = [...messages].reverse().find((m) => m.role === "assistant" && m.content !== "已生成自测题，见下方结果区。");
                if (!lastMsg) return;
                handleDocumentPrint({
                  title: "智能问答回答",
                  subject: selectedSubject,
                  scopeLabel: formatSourceScope(selectedMaterials.map((m) => m.relativePath)),
                  generatedAt: new Date().toISOString(),
                  content: lastMsg.content,
                  sources: qaHits,
                  includeSources: qaIncludeSources,
                  filename: getExportFilename("智能问答", ".pdf"),
                });
              }}>
                打印 / 另存为 PDF
              </button>
            </div>
          )}
          {selfTestResult && (
            <section className="self-test-result">
              <div className="self-test-header">
                <strong>自测题结果</strong>
                <span className="self-test-subject">{selfTestResult.subject}</span>
                <span className="self-test-meta">
                  {formatTime(selfTestResult.generatedAt)}
                </span>
                <span className="self-test-meta">范围：{selfTestResult.scopeLabel}</span>
              </div>
              {selfTestResult.quizSettings?.typeConfigs && (
                <div className="self-test-config-row">
                  {selfTestResult.quizSettings.typeConfigs.filter((tc) => tc.enabled).map((tc) => (
                    <span key={tc.type} className="self-test-config-chip">
                      {typeLabels[tc.type]} {tc.count}
                    </span>
                  ))}
                  <span className="self-test-config-chip">
                    {selfTestResult.quizSettings.answerMode === "inline" ? "每题后显示答案解析" :
                     selfTestResult.quizSettings.answerMode === "end" ? "答案解析统一放卷尾" :
                     "练习版+解析版"}
                  </span>
                </div>
              )}
              {selfTestResult.quizSettings?.answerMode === "dual" && (
                <div className="quiz-tabs">
                  <button
                    type="button"
                    className={`quiz-tab${activeQuizTab === "practice" ? " quiz-tab-active" : ""}`}
                    onClick={() => { setActiveQuizTab("practice"); setShowAnswer(false); }}
                  >
                    练习版
                  </button>
                  <button
                    type="button"
                    className={`quiz-tab${activeQuizTab === "answer" ? " quiz-tab-active" : ""}`}
                    onClick={() => { setActiveQuizTab("answer"); setShowAnswer(true); }}
                  >
                    解析版
                  </button>
                </div>
              )}
              <div className="self-test-content">
                {renderAssistantContent(getQuizDisplayContent(), selfTestResult.hits.length, handleSelfTestSourceClick)}
              </div>
              {selfTestResult.hits.length > 0 && (
                <div className="study-guide-sources" style={{ marginTop: 0 }}>
                  <button type="button" className="source-collapse-toggle" onClick={() => setSelfTestSourcesExpanded((v) => !v)}>
                    {selfTestSourcesExpanded ? "收起" : "展开"}来源依据（{selfTestResult.hits.length}）
                  </button>
                  {selfTestSourcesExpanded && (
                    <div className="source-collapse-list">
                      {selfTestResult.hits.map((hit, index) => {
                        const target = hitToPreviewTarget(hit);
                        const fileName = metadataText(hit.metadata, "file_name") || metadataText(hit.metadata, "source_path") || "未知来源";
                        const page = metadataText(hit.metadata, "page_number");
                        const slide = metadataText(hit.metadata, "slide_number");
                        const location = page
                          ? `第 ${page} 页`
                          : slide
                            ? `第 ${slide} 张幻灯片`
                            : hit.source || "位置未知";
                        return (
                          <article
                            className={`${highlightedSelfTestSourceIndex === index ? " self-test-source-highlight" : ""}${target ? " study-guide-source-action" : ""}`}
                            key={`st-source-${index}`}
                            ref={(node) => { selfTestSourceRefs.current[index] = node; }}
                            role={target ? "button" : undefined}
                            tabIndex={target ? 0 : undefined}
                            onClick={target ? () => handleReferencePreviewClick(target, "自测题来源", [target], 0) : undefined}
                            onKeyDown={target ? (event) => {
                              if (event.key === "Enter" || event.key === " ") {
                                event.preventDefault();
                                handleReferencePreviewClick(target, "自测题来源", [target], 0);
                              }
                            } : undefined}
                          >
                            <span>
                              <strong>#{index + 1} {fileName}</strong>
                              <span>{location}</span>
                            </span>
                            <p>{shortenText(hit.text || "")}</p>
                          </article>
                        );
                      })}
                    </div>
                  )}
                </div>
              )}
              <div className="self-test-actions">
                {selfTestResult.quizSettings?.answerMode === "dual" && (
                  <button
                    type="button"
                    className={`self-test-eye-btn${showAnswer ? " eye-open" : " eye-closed"}`}
                    onClick={handleEyeToggle}
                    title={showAnswer ? "切换为练习版" : "切换为解析版"}
                  >
                    {showAnswer ? "👁" : "👁‍🗨"}
                  </button>
                )}
                <label className="export-include-label">
                  <input
                    type="checkbox"
                    checked={selfTestIncludeSources}
                    onChange={() => setSelfTestIncludeSources((v) => !v)}
                  />
                  附带来源
                </label>
                <button type="button" onClick={handleSelfTestExportWord} className="self-test-export-word">
                  导出 Word
                </button>
                <button type="button" onClick={handleSelfTestPrint} className="self-test-print-pdf">
                  打印 / 另存为 PDF
                </button>
              </div>
            </section>
          )}
          {longformResult && (
            <section className="longform-result">
              <div className="longform-header">
                <strong>资料整理结果</strong>
                <span className="longform-subject">{selectedSubject}</span>
                <span className="longform-meta">
                  {longformType === "analysis" ? "深度分析" :
                   longformType === "study_notes" ? "学习笔记" :
                   longformType === "report" ? "综合报告" :
                   longformType === "review" ? "复习整理" :
                   longformType === "outline" ? "知识框架" : longformType}
                </span>
                <span className="longform-meta">目标 {longformTargetLength} 字</span>
              </div>
              {longformResult.stats && (
                <div className="longform-stats-row">
                  <span className="longform-stat-chip">使用片段 {longformResult.stats.used_chunks}/{longformResult.stats.total_chunks}</span>
                  <span className="longform-stat-chip">分组 {longformResult.stats.groups_count}</span>
                </div>
              )}
              {longformResult.warnings && longformResult.warnings.length > 0 && (
                <div className="longform-warnings">
                  {longformResult.warnings.map((w, i) => (
                    <p key={i} className="longform-warning">{w}</p>
                  ))}
                </div>
              )}
              <div className="longform-content">
                {renderRichMarkdown(longformResult.content, longformResult.sources.length, () => {
                  setLongformSourcesExpanded(true);
                })}
              </div>
              {longformResult.outline && (
                <div className="longform-collapse-section">
                  <button type="button" className="source-collapse-toggle" onClick={() => setLongformOutlineExpanded((v) => !v)}>
                    {longformOutlineExpanded ? "收起" : "展开"}大纲
                  </button>
                  {longformOutlineExpanded && (
                    <div className="longform-collapse-body longform-outline-body">
                      {renderMarkdown(longformResult.outline)}
                    </div>
                  )}
                </div>
              )}
              {longformResult.group_summaries && longformResult.group_summaries.length > 0 && (
                <div className="longform-collapse-section">
                  <button type="button" className="source-collapse-toggle" onClick={() => setLongformSummariesExpanded((v) => !v)}>
                    {longformSummariesExpanded ? "收起" : "展开"}分组摘要（{longformResult.group_summaries.length}）
                  </button>
                  {longformSummariesExpanded && (
                    <div className="longform-collapse-body">
                      {longformResult.group_summaries.map((gs, i) => (
                        <article key={i} className="longform-summary-item">
                          <strong>{gs.source_label || `第 ${gs.group_index || i + 1} 组`}</strong>
                          <span className="longform-summary-chips">
                            {gs.chunks_count !== undefined && <span>{gs.chunks_count} 片段</span>}
                          </span>
                          <p>{gs.summary}</p>
                        </article>
                      ))}
                    </div>
                  )}
                </div>
              )}
              {longformResult.sources.length > 0 && (
                <div className="longform-collapse-section">
                  <button type="button" className="source-collapse-toggle" onClick={() => setLongformSourcesExpanded((v) => !v)}>
                    {longformSourcesExpanded ? "收起" : "展开"}来源资料（{longformResult.sources.length}）
                  </button>
                  {longformSourcesExpanded && (
                    <div className="source-collapse-list">
                      {longformResult.sources.map((source, index) => {
                        const target = hitToPreviewTarget(source);
                        const fileName = metadataText(source.metadata, "file_name") || metadataText(source.metadata, "source_path") || "未知来源";
                        const page = metadataText(source.metadata, "page_number");
                        const slide = metadataText(source.metadata, "slide_number");
                        const location = page
                          ? `第 ${page} 页`
                          : slide
                            ? `第 ${slide} 张幻灯片`
                            : source.source || "位置未知";
                        return (
                          <article
                            className={`${target ? "study-guide-source-action" : ""}`}
                            key={`lf-source-${index}`}
                            role={target ? "button" : undefined}
                            tabIndex={target ? 0 : undefined}
                            onClick={target ? () => handleReferencePreviewClick(target, "整理来源", [target], 0) : undefined}
                            onKeyDown={target ? (event) => {
                              if (event.key === "Enter" || event.key === " ") {
                                event.preventDefault();
                                handleReferencePreviewClick(target, "整理来源", [target], 0);
                              }
                            } : undefined}
                          >
                            <span>
                              <strong>#{index + 1} {fileName}</strong>
                              <span>{location}</span>
                            </span>
                            <p>{shortenText(source.text || "")}</p>
                          </article>
                        );
                      })}
                    </div>
                  )}
                </div>
              )}
              <div className="longform-actions">
                <label className="export-include-label">
                  <input
                    type="checkbox"
                    checked={longformIncludeSources}
                    onChange={() => setLongformIncludeSources((v) => !v)}
                  />
                  附带来源
                </label>
                <button type="button" onClick={handleLongformExportWord} className="longform-export-word">
                  导出 Word
                </button>
                <button type="button" onClick={handleLongformPrintPdf} className="longform-print-pdf">
                  打印 / 另存为 PDF
                </button>
              </div>
            </section>
          )}
          {qaWarning && (
            <div className="qa-warning">
              <p>{qaWarning}</p>
              {shouldShowBuildActions(qaErrorType, qaWarning) && (
                <div className="build-actions">
                  <button
                    className="primary-build"
                    type="button"
                    onClick={() => handleBuildIndex("update")}
                    disabled={indexLoadingMode !== null || indexCorrupted}
                    title={indexCorrupted ? indexCorruptedHint : undefined}
                  >
                    {indexLoadingMode === "update" ? "添加/更新中..." : "添加/更新当前范围到知识库"}
                  </button>
                  <button
                    className={indexCorrupted ? "primary-build rebuild-highlight" : ""}
                    type="button"
                    onClick={() => handleBuildIndex("reset")}
                    disabled={indexLoadingMode !== null}
                  >
                    {indexLoadingMode === "reset" ? "重建中..." : "重建当前范围知识库"}
                  </button>
                </div>
              )}
            </div>
          )}
          <form className="ask-box" onSubmit={handleAsk} ref={askFormRef}>
            <div className="qa-input-prompts">
              <button
                type="button"
                onClick={handleSelfTestPromptClick}
                disabled={qaLoading}
              >
                <span aria-hidden="true">📝</span>
                生成自测题
              </button>
              <button
                type="button"
                onClick={() => setLongformSettingsOpen(true)}
                disabled={longformLoading}
              >
                <span aria-hidden="true">📄</span>
                资料整理
                <span className="mode-badge mode-badge-deep">深度模式</span>
              </button>
            </div>
            <textarea
              ref={qaInputRef}
              value={question}
              onChange={(event) => setQuestion(event.target.value)}
              onKeyDown={(event) => {
                if (event.nativeEvent.isComposing) return;
                if (event.key !== "Enter") return;
                if (event.shiftKey) return;
                event.preventDefault();
                if (!question.trim() || qaLoading) return;
                askFormRef.current?.requestSubmit();
              }}
              placeholder="输入你的问题，或选择上方快捷问题..."
              rows={3}
            />
            <button type="submit" disabled={qaLoading}>
              {qaLoading ? "回答中..." : "发送问题"}
            </button>
          </form>
        </section>
      </section>
      {showQuizModal && (
        <div className="modal-overlay" onClick={handleCancelQuiz}>
          <div className="quiz-modal" onClick={(event) => event.stopPropagation()}>
            <div className="quiz-modal-header">
              <h2>自测题生成设置</h2>
              <button type="button" className="quiz-modal-close" onClick={handleCancelQuiz}>×</button>
            </div>
            <div className="quiz-modal-subtitle">
              {selectedSubject} · {formatSourceScope(selectedMaterials.map((m) => m.relativePath))}
            </div>
            <div className="quiz-modal-body">
              <div className="quiz-modal-section">
                <h3>题型管理与顺序</h3>
                <div className="quiz-type-list">
                  {quizSettings.typeConfigs.map((tc, index) => (
                    <div
                      key={tc.type}
                      className={`quiz-type-row${!tc.enabled ? " quiz-type-row-disabled" : ""}${dragOverIndex === index ? " quiz-type-row-dragover" : ""}`}
                      draggable
                      onDragStart={(event) => {
                        event.dataTransfer.effectAllowed = "move";
                        setDragIndex(index);
                      }}
                      onDragOver={(event) => {
                        event.preventDefault();
                        event.dataTransfer.dropEffect = "move";
                        setDragOverIndex(index);
                      }}
                      onDragEnd={() => {
                        if (dragIndex !== null && dragOverIndex !== null && dragIndex !== dragOverIndex) {
                          handleQuizTypeMoveIndex(dragIndex, dragOverIndex);
                        }
                        setDragIndex(null);
                        setDragOverIndex(null);
                      }}
                    >
                      <span className="quiz-drag-handle" aria-label="拖动排序">≡</span>
                      <label className="quiz-type-checkbox">
                        <input
                          type="checkbox"
                          checked={tc.enabled}
                          onChange={() => handleQuizTypeToggle(tc.type)}
                        />
                        <span>{typeLabels[tc.type]}</span>
                      </label>
                      <div className="quiz-type-count">
                        <button
                          type="button"
                          className="quiz-step-btn"
                          onClick={() => handleQuizTypeCountAdj(tc.type, -1)}
                          disabled={!tc.enabled || tc.count <= 1}
                        >−</button>
                        <input
                          type="number"
                          min={0}
                          max={20}
                          value={tc.count}
                          disabled={!tc.enabled}
                          onChange={(event) => {
                            const val = parseInt(event.target.value, 10);
                            if (!isNaN(val)) handleQuizTypeCountSet(tc.type, val);
                          }}
                        />
                        <button
                          type="button"
                          className="quiz-step-btn"
                          onClick={() => handleQuizTypeCountAdj(tc.type, 1)}
                          disabled={!tc.enabled || tc.count >= 20}
                        >+</button>
                      </div>
                      <div className="quiz-type-position">{index + 1}</div>
                    </div>
                  ))}
                </div>
                <p className="quiz-type-hint">
                  {quizSettings.typeConfigs.filter((tc) => tc.enabled).length === 0
                    ? "请至少选择一种题型"
                    : `共 ${quizSettings.typeConfigs.filter((tc) => tc.enabled).reduce((s, tc) => s + tc.count, 0)} 题`}
                </p>
              </div>
              <div className="quiz-modal-section">
                <h3>答案与解析排版</h3>
                <div className="quiz-answer-modes">
                  {answerModeOptions.map((opt) => (
                    <label key={opt.value} className={`quiz-answer-card${quizSettings.answerMode === opt.value ? " quiz-answer-card-active" : ""}`}>
                      <input
                        type="radio"
                        name="answerMode"
                        value={opt.value}
                        checked={quizSettings.answerMode === opt.value}
                        onChange={() => setQuizSettings((prev) => ({ ...prev, answerMode: opt.value }))}
                      />
                      <div className="quiz-answer-card-content">
                        <strong>{opt.label}</strong>
                        <span>{opt.desc}</span>
                      </div>
                    </label>
                  ))}
                </div>
              </div>
            </div>
            <div className="quiz-modal-footer">
              <button type="button" className="quiz-cancel-btn" onClick={handleCancelQuiz}>取消</button>
              <button
                type="button"
                className="quiz-start-btn"
                onClick={handleStartQuiz}
                disabled={quizSettings.typeConfigs.filter((tc) => tc.enabled).length === 0}
              >
                开始出题
              </button>
            </div>
          </div>
        </div>
      )}
      {longformSettingsOpen && (
        <div className="modal-overlay" onClick={() => setLongformSettingsOpen(false)}>
          <div className="longform-modal" onClick={(event) => event.stopPropagation()}>
            <div className="longform-modal-header">
              <h2>资料整理与长文分析</h2>
              <button type="button" className="longform-modal-close" onClick={() => setLongformSettingsOpen(false)}>×</button>
            </div>
            <div className="longform-modal-subtitle">
              当前范围：{selectedSubject} · {currentScopeText}
            </div>
            <div className="longform-modal-body">
              {/* 整理目标 */}
              <div className="longform-modal-section">
                <h3>整理目标</h3>
                <div className="longform-target-row">
                  <select
                    className="longform-type-select"
                    value={longformType}
                    onChange={(event) => setLongformType(event.target.value as LongformType)}
                  >
                    <option value="analysis">深度分析</option>
                    <option value="study_notes">学习笔记</option>
                    <option value="report">综合报告</option>
                    <option value="review">读后感 / 心得体会</option>
                    <option value="outline">提纲</option>
                  </select>
                  <div className="longform-type-tags">
                    {[
                      { value: "analysis" as const, label: "深度分析" },
                      { value: "study_notes" as const, label: "学习笔记" },
                      { value: "outline" as const, label: "提纲" },
                    ].map((tag) => (
                      <button
                        key={tag.value}
                        type="button"
                        className={`longform-type-tag${longformType === tag.value ? " active" : ""}`}
                        onClick={() => setLongformType(tag.value)}
                      >
                        {tag.label}
                      </button>
                    ))}
                  </div>
                </div>
                <p className="longform-type-desc">
                  {longformType === "analysis" && "对资料内容进行系统深入的分析与解读，适合梳理核心逻辑与重难点。"}
                  {longformType === "study_notes" && "整理成便于复习与回顾的学习笔记。"}
                  {longformType === "report" && "生成结构化、偏正式的综合报告。"}
                  {longformType === "review" && "以个人视角总结学习心得与体会。"}
                  {longformType === "outline" && "提炼知识框架与结构大纲。"}
                </p>
              </div>

              {/* 分割线 */}
              <div className="longform-divider" />

              {/* 输出字数 */}
              <div className="longform-modal-section">
                <h3>输出字数</h3>
                <div className="longform-length-presets">
                  {[1500, 3000, 5000].map((len) => (
                    <button
                      key={len}
                      type="button"
                      className={`longform-length-preset${longformTargetLength === len && !longformCustomLength ? " active" : ""}`}
                      onClick={() => {
                        setLongformTargetLength(len);
                        setLongformCustomLength("");
                      }}
                    >
                      {len} 字
                    </button>
                  ))}
                  <button
                    type="button"
                    className={`longform-length-preset${longformCustomLength !== "" ? " active" : ""}`}
                    onClick={() => {
                      setLongformCustomLength("3000");
                      setLongformTargetLength(3000);
                    }}
                  >
                    自定义
                  </button>
                </div>
                {longformCustomLength !== "" && (
                  <div className="longform-custom-length-row">
                    <input
                      type="number"
                      className="longform-custom-length-input"
                      value={longformCustomLength}
                      min={500}
                      max={15000}
                      onChange={(event) => {
                        const val = event.target.value;
                        setLongformCustomLength(val);
                        const num = Number(val);
                        if (val && !isNaN(num) && num >= 500 && num <= 15000) {
                          setLongformTargetLength(num);
                        }
                      }}
                      placeholder="输入字数..."
                    />
                    <span className="longform-custom-length-unit">字</span>
                    {(() => {
                      const num = Number(longformCustomLength);
                      if (longformCustomLength && (isNaN(num) || num < 500)) {
                        return <span className="longform-length-warning">最少 500 字</span>;
                      }
                      if (longformCustomLength && num > 15000) {
                        return <span className="longform-length-warning">最多 15000 字</span>;
                      }
                      if (longformCustomLength && num > 8000) {
                        return <span className="longform-length-hint">字数较长，生成时间会明显增加，且可能因模型窗口限制被截断。</span>;
                      }
                      return null;
                    })()}
                  </div>
                )}
                <p className="longform-length-footnote">字数越长，生成越慢；长文分析建议选择 3000 字以上。</p>
              </div>

              {/* 分割线 */}
              <div className="longform-divider" />

              {/* 高级设置 */}
              <div className="longform-modal-section">
                <h3>高级设置</h3>
                <label className="longform-checkbox-label">
                  <input
                    type="checkbox"
                    checked={longformIncludeSources}
                    onChange={() => setLongformIncludeSources((v) => !v)}
                  />
                  <span>
                    在生成结果中附带原文溯源引用
                    <span className="longform-checkbox-hint">（推荐，方便核对资料）</span>
                  </span>
                </label>
              </div>

              {/* 分割线 */}
              <div className="longform-divider" />

              {/* 补充要求 */}
              <div className="longform-modal-section">
                <h3>补充要求 <span className="longform-section-note">（可选）</span></h3>
                <textarea
                  className="longform-instruction-input"
                  value={longformInstruction}
                  onChange={(event) => {
                    const val = event.target.value;
                    if (val.length <= 300) {
                      setLongformInstruction(val);
                    }
                  }}
                  placeholder="例如：重点梳理第三章的定理证明，或使用严谨的学术语气……"
                  rows={3}
                  maxLength={300}
                />
                <div className="longform-char-count">{longformInstruction.length}/300</div>
              </div>
            </div>
            <div className="longform-modal-footer">
              <button type="button" className="longform-cancel-btn" onClick={() => setLongformSettingsOpen(false)}>取消</button>
              <button
                type="button"
                className="longform-start-btn"
                onClick={handleLongformAnalysis}
                disabled={(() => {
                  if (longformLoading) return true;
                  if (longformCustomLength) {
                    const num = Number(longformCustomLength);
                    if (!longformCustomLength || isNaN(num) || num < 500 || num > 15000) return true;
                  }
                  return false;
                })()}
              >
                {longformLoading ? "正在整理…" : "开始整理"}
              </button>
            </div>
          </div>
        </div>
      )}
      {longformLoading && !longformSettingsOpen && (
        <div className="longform-loading-bar">
          <span>正在整理资料…</span>
        </div>
      )}
      {printPayload && (
        <div className="print-document">
          <div className="print-document-content">
            <h1>{printPayload.title}</h1>
            <p className="print-meta">科目：{printPayload.subject}</p>
            {printPayload.scopeLabel && <p className="print-meta">资料范围：{printPayload.scopeLabel}</p>}
            <p className="print-meta">生成时间：{printPayload.generatedAt}</p>
            <div className="print-body">
              {renderRichMarkdown(printPayload.content, 0, () => {})}
            </div>
            {printPayload.includeSources && printPayload.sources.length > 0 && (
              <div className="print-sources">
                <h2>参考来源</h2>
                {printPayload.sources.slice(0, 10).map((source, i) => {
                  const meta = (source.metadata || {}) as Record<string, unknown>;
                  const fileName = String(meta.file_name || meta.source_path || "未知来源");
                  const page = meta.page_number;
                  const slide = meta.slide_number;
                  const location = page ? `第 ${page} 页` : slide ? `第 ${slide} 张幻灯片` : "位置未知";
                  return <p key={i} className="print-source-item">[{i + 1}] {fileName}，{location}</p>;
                })}
              </div>
            )}
          </div>
        </div>
      )}
      {sourcePreviewOpen && (
        <div className="source-preview-backdrop" role="presentation" onClick={closeSourcePreview}>
          <section
            className="source-preview-modal"
            role="dialog"
            aria-modal="true"
            aria-label="来源页预览"
            onClick={(event) => event.stopPropagation()}
          >
            <button
              className="source-preview-close"
              type="button"
              aria-label="关闭来源页预览"
              onClick={closeSourcePreview}
            >
              ×
            </button>
            <div className="source-preview-layout">
              <div className="source-preview-image-panel">
                <div className="source-preview-zoom-toolbar">
                  <button
                    type="button"
                    className="source-preview-zoom-btn"
                    onClick={() => setPreviewZoom((v) => Math.max(0.5, (typeof v === "number" ? v : 1) - 0.25))}
                    disabled={typeof previewZoom === "number" && previewZoom <= 0.5}
                    title="缩小"
                  >−</button>
                  <button
                    type="button"
                    className={`source-preview-zoom-btn${previewZoom === "fit-width" ? " active" : ""}`}
                    onClick={() => setPreviewZoom("fit-width")}
                    title="适合宽度"
                  >适合宽度</button>
                  <button
                    type="button"
                    className={`source-preview-zoom-btn${previewZoom === 1 ? " active" : ""}`}
                    onClick={() => setPreviewZoom(1)}
                    title="100%"
                  >100%</button>
                  <button
                    type="button"
                    className="source-preview-zoom-btn"
                    onClick={() => setPreviewZoom((v) => Math.min(3, (typeof v === "number" ? v : 1) + 0.25))}
                    disabled={typeof previewZoom === "number" && previewZoom >= 3}
                    title="放大"
                  >+</button>
                  {typeof previewZoom === "number" && (
                    <span className="source-preview-zoom-label">{Math.round(previewZoom * 100)}%</span>
                  )}
                </div>
                <div className="source-preview-image-scroll">
                {previewImageMessage ? (
                  <div className="source-preview-error">
                    {previewImageMessage}
                  </div>
                ) : imageError ? (
                  <div className="source-preview-error">
                    页面预览生成失败。若该来源是 PPT/PPTX，可能是首次转换较慢或 LibreOffice 转换失败。
                  </div>
                ) : (
                  <img
                    src={previewImageUrl}
                    alt={`${previewFileName} ${previewLocation}`}
                    onError={() => setImageError(true)}
                    className={previewZoom === "fit-width" ? "source-preview-page-image-fit" : "source-preview-page-image-zoom"}
                    style={typeof previewZoom === "number" ? { width: `${previewZoom * 100}%`, maxWidth: "none" } : undefined}
                  />
                )}
                </div>
              </div>
              <aside className="source-preview-info-panel">
                <div className="source-preview-info-head">
                  <span>{previewReferenceTarget?.title || "来源依据"}</span>
                  <strong title={previewFileName}>{previewFileName}</strong>
                  <span className="source-preview-meta-line">
                    {(previewRawFileType || "未知").toUpperCase()}{"  "}{previewLocation}
                  </span>
                  {previewHit && previewHitIndex !== null && (
                    <div className="source-preview-evidence-meta">
                      <span>依据 {previewHitIndex + 1} / {qaHits.length}</span>
                      <span className={`evidence-level ${previewEvidenceLevel.className}`}>
                        {previewEvidenceLevel.text}
                      </span>
                    </div>
                  )}
                </div>
                {previewHit && previewHitIndex !== null && (
                  <div className="source-preview-nav">
                    <button
                      type="button"
                      onClick={showPreviousPreviewHit}
                      disabled={previewHitIndex === 0}
                    >
                      上一条依据
                    </button>
                    <button
                      type="button"
                      onClick={showNextPreviewHit}
                      disabled={previewHitIndex === qaHits.length - 1}
                    >
                      下一条依据
                    </button>
                  </div>
                )}
                {previewReferenceTarget && previewReferenceIndex !== null && (
                  <div className="source-preview-nav">
                    <button
                      type="button"
                      onClick={showPreviousReferencePreview}
                      disabled={previewReferenceIndex === 0 || previewReferenceList.length <= 1}
                    >
                      上一条
                    </button>
                    <button
                      type="button"
                      onClick={showNextReferencePreview}
                      disabled={previewReferenceIndex >= previewReferenceList.length - 1 || previewReferenceList.length <= 1}
                    >
                      下一条
                    </button>
                  </div>
                )}
                <section>
                  <h3>{previewSnippetTitle}</h3>
                  <p className={isPreviewSnippetExpanded ? "expanded" : ""}>
                    {hasPreviewSnippetText
                      ? renderHighlightedSnippet(previewSnippetText, previewHighlightTerms)
                      : "这页暂无可识别文字，建议直接查看左侧页面。"}
                  </p>
                  {hasPreviewSnippetText && (
                    <button
                      className="source-preview-text-toggle"
                      type="button"
                      onClick={() => setIsPreviewSnippetExpanded((value) => !value)}
                    >
                      {isPreviewSnippetExpanded ? "收起完整文本 ▲" : "查看本页完整文本 ▼"}
                    </button>
                  )}
                </section>
              </aside>
            </div>
          </section>
        </div>
      )}
      {showAiSettingsModal && (
        <div
          className="ai-settings-backdrop"
          role="presentation"
          onClick={() => setShowAiSettingsModal(false)}
        >
          <section
            className="ai-settings-modal"
            role="dialog"
            aria-modal="true"
            aria-label="AI 设置"
            onClick={(event) => event.stopPropagation()}
          >
            <button
              className="ai-settings-close"
              type="button"
              aria-label="关闭 AI 设置"
              onClick={() => setShowAiSettingsModal(false)}
            >
              ×
            </button>
            <div className="ai-settings-modal-head">
              <div>
                <strong>AI 设置</strong>
                <span>{aiSettingsDraft.enabled ? "当前已开启" : "当前已关闭"}</span>
              </div>
              <label className="ai-switch" aria-label="启用 AI">
                <input
                  className="ai-switch-input"
                  type="checkbox"
                  checked={aiSettingsDraft.enabled}
                  onChange={(event) => setAiSettingsDraft((draft) => ({
                    ...draft,
                    enabled: event.target.checked
                  }))}
                  disabled={aiSettingsLoading || aiSettingsSaving}
                />
                <span className="ai-switch-slider" aria-hidden="true" />
              </label>
            </div>
            <form className="ai-settings-form" onSubmit={handleAiSettingsSave}>
              <label>
                <span>Provider</span>
                <select
                  value={aiSettingsDraft.provider}
                  onChange={(event) => {
                    const nextProvider = event.target.value as AiProvider;
                    const nextProfile = getAiProviderProfile(aiSettings, nextProvider);
                    if (nextProvider === "deepseek") {
                      setCustomModelExpanded(false);
                    }
                    setAiSettingsDraft((draft) => ({
                      ...draft,
                      provider: nextProvider,
                      base_url: nextProfile.base_url,
                      model:
                        nextProvider === "deepseek" && !nextProfile.model.trim()
                          ? "deepseek-v4-flash"
                          : nextProfile.model,
                      api_key: ""
                    }));
                  }}
                  disabled={aiSettingsLoading || aiSettingsSaving}
                >
                  <option value="deepseek">DeepSeek</option>
                  <option value="openai_compatible">OpenAI-compatible</option>
                </select>
              </label>
              <label>
                <span>Base URL</span>
                <input
                  type="text"
                  value={aiSettingsDraft.base_url}
                  onChange={(event) => setAiSettingsDraft((draft) => ({
                    ...draft,
                    base_url: event.target.value
                  }))}
                  placeholder="https://api.deepseek.com"
                  disabled={aiSettingsLoading || aiSettingsSaving}
                />
              </label>
              <label>
                <span>Model</span>
                {aiSettingsDraft.provider === "deepseek" ? (
                  <div className="ai-model-picker">
                    {deepseekModelOptions.map((option) => (
                      <button
                        key={option.value}
                        type="button"
                        className={`ai-model-option ${aiSettingsDraft.model === option.value ? "selected" : ""}`}
                        onClick={() => {
                          setCustomModelExpanded(false);
                          setAiSettingsDraft((draft) => ({
                            ...draft,
                            model: option.value
                          }));
                        }}
                        disabled={aiSettingsLoading || aiSettingsSaving}
                      >
                        <strong>{option.title}</strong>
                        <span className="ai-model-name">{option.modelName}</span>
                        <span>{option.description}</span>
                      </button>
                    ))}
                    <button
                      type="button"
                      className="custom-model-toggle"
                      onClick={() => setCustomModelExpanded((expanded) => !expanded)}
                      disabled={aiSettingsLoading || aiSettingsSaving}
                    >
                      {showCustomDeepSeekModelInput ? "收起自定义设置" : "自定义模型名称"}
                    </button>
                    {showCustomDeepSeekModelInput && (
                      <input
                        type="text"
                        value={aiSettingsDraft.model}
                        onChange={(event) => setAiSettingsDraft((draft) => ({
                          ...draft,
                          model: event.target.value
                        }))}
                        placeholder="自定义 DeepSeek 模型名"
                        disabled={aiSettingsLoading || aiSettingsSaving}
                      />
                    )}
                  </div>
                ) : (
                  <>
                    <input
                      type="text"
                      value={aiSettingsDraft.model}
                      onChange={(event) => setAiSettingsDraft((draft) => ({
                        ...draft,
                        model: event.target.value
                      }))}
                      placeholder="gpt-4.1-mini"
                      disabled={aiSettingsLoading || aiSettingsSaving}
                    />
                    <span className="ai-settings-help">
                      请输入该 API 服务支持的模型名，模型名称以你的 API 服务商说明为准。
                    </span>
                  </>
                )}
              </label>
              <label>
                <span>API Key</span>
                <input
                  type="password"
                  value={aiSettingsDraft.api_key}
                  onChange={(event) => setAiSettingsDraft((draft) => ({
                    ...draft,
                    api_key: event.target.value
                  }))}
                  placeholder={selectedAiProfile.has_api_key ? "已保存；填写可替换" : "填写新的 API Key"}
                  disabled={aiSettingsLoading || aiSettingsSaving}
                  autoComplete="off"
                />
              </label>
              <div className="security-status-badge">
                {selectedAiProfile.has_api_key ? "🛡 已保存 API Key，填写新 Key 可替换" : "🛡 尚未保存 API Key"}
              </div>
              <button className="ai-settings-submit" type="submit" disabled={aiSettingsLoading || aiSettingsSaving}>
                {aiSettingsSaving ? "保存中..." : "保存设置"}
              </button>
              {aiSettingsNotice && <p>{aiSettingsNotice}</p>}
            </form>
          </section>
        </div>
      )}
    </main>
  );
}

export default App;
