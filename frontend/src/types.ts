export type MaterialStatus = "已建库" | "未建库" | "待转换" | "索引异常";

export interface Subject {
  name: string;
}

export interface CreateSubjectResponse {
  name: string;
  materials_dir?: string;
  outputs_dir?: string;
}

export interface Material {
  id: string;
  fileName: string;
  relativePath: string;
  fileType: string;
  fileExtension?: string;
  sizeLabel: string;
  chapter?: string;
  group?: string;
  status: MaterialStatus;
  category: string;
  lastUsed: string;
  conversionStatus?: string;
  convertedPptx?: string | null;
}

export interface SubjectStatus {
  subject: string;
  file_count: number;
  total_size_bytes: number;
  indexed_count: number;
  index_status?: "ready" | "empty" | "corrupted" | "error";
  deepseek_configured: boolean;
  materials_dir: string;
  outputs_dir: string;
  warning?: string;
}

export type AiProvider = "deepseek" | "openai_compatible";

export interface AiProviderProfile {
  base_url: string;
  model: string;
  has_api_key: boolean;
}

export interface AiSettings {
  enabled: boolean;
  provider: AiProvider;
  base_url: string;
  model: string;
  has_api_key: boolean;
  profiles?: Record<AiProvider, AiProviderProfile>;
}

export interface AiSettingsRequest {
  enabled: boolean;
  provider: AiProvider;
  base_url: string;
  model: string;
  api_key?: string;
}

export interface SourceReference {
  id: string;
  fileName: string;
  location: string;
  similarity: number;
  summary: string;
}

export interface Hit {
  rank?: number;
  source?: string;
  text: string;
  metadata: Record<string, unknown>;
  similarity?: number | null;
  hybrid_score?: number | null;
  keyword_score?: number | null;
}

export interface QARequest {
  question: string;
  source_filters: string[];
  top_k: number;
  use_deepseek: boolean;
}

export interface QAResponse {
  answer: string;
  warning: string | null;
  hits: Hit[];
  error_type?: string;
}

export interface SelfTestRequest {
  source_filters: string[];
  type_configs: Array<Pick<QuizTypeConfig, "type" | "count">>;
  answer_mode: QuizAnswerMode;
  generation_mode: QuizGenerationMode;
}

export interface SnippetKeywordsRequest {
  text: string;
}

export interface SnippetKeywordsResponse {
  keywords: string[];
  warning?: string | null;
}

export interface BuildIndexRequest {
  mode: "update" | "reset";
  scope: "all" | "selected";
  files: string[];
}

export interface BuildIndexResponse {
  success: boolean;
  mode?: "update" | "reset";
  scope?: "all" | "selected";
  file_count: number;
  chunk_count: number;
  chroma_count: number;
  ppt_conversion: {
    success_count: number;
    failure_count: number;
    failures: unknown[];
  };
  messages: string[];
  warning?: string;
  error?: string | null;
  error_type?: string;
}

export interface StudyGuideSource {
  rank?: string;
  label?: string;
  source_path?: string;
  file_name?: string;
  file_type?: string;
  page_number?: number | string | null;
  slide_number?: number | string | null;
  location?: string;
  text?: string;
}

export interface StudyGuideRequest {
  source_filters: string[];
  use_deepseek: boolean;
  force_refresh: boolean;
}

export interface StudyGuideResponse {
  success: boolean;
  content: string;
  sources: StudyGuideSource[];
  references?: StudyGuideSource[];
  cached: boolean;
  warning: string | null;
}

export type OverviewSource = StudyGuideSource;

export interface OverviewRequest {
  source_filters: string[];
  use_deepseek: boolean;
  force_refresh: boolean;
}

export interface OverviewResponse {
  success: boolean;
  content: string;
  sources: OverviewSource[];
  references?: OverviewSource[];
  cached: boolean;
  warning: string | null;
}

export interface UploadMaterialsResponse {
  success: boolean;
  saved_files: unknown[];
  message: string;
  warning?: string | null;
  error?: string | null;
}

export interface DeleteMaterialResponse {
  success: boolean;
  message: string;
  deleted_relative_path: string;
  warning?: string | null;
  error?: string | null;
}

export interface RenameMaterialResponse {
  success: boolean;
  message: string;
  material: unknown;
  warning?: string | null;
  error?: string | null;
}

export interface ConvertPptResponse {
  success: boolean;
  message: string;
  converted_pptx: string;
  archived_original_ppt: string;
  warning?: string | null;
  error?: string | null;
}

export type QuizQuestionType = "choice" | "fill" | "essay";
export type QuizAnswerMode = "inline" | "end" | "dual";
export type QuizGenerationMode = "single_page" | "fusion";

export interface QuizTypeConfig {
  type: QuizQuestionType;
  enabled: boolean;
  count: number;
}

export interface QuizSettings {
  typeConfigs: QuizTypeConfig[];
  answerMode: QuizAnswerMode;
  generationMode: QuizGenerationMode;
}

export interface SelfTestResult {
  subject: string;
  scopeLabel: string;
  sourceFilters: string[];
  generatedAt: string;
  content: string;
  hits: Hit[];
  quizSettings?: QuizSettings;
}

export interface ExportSelfTestRequest {
  subject: string;
  scope_label: string;
  generated_at: string;
  content: string;
  sources: Hit[];
  include_sources?: boolean;
  filename?: string;
}

export interface ExportDocumentRequest {
  title: string;
  subject: string;
  scope_label: string;
  generated_at: string;
  content: string;
  sources: Hit[];
  include_sources: boolean;
  filename_prefix: string;
  filename?: string;
}

export interface ChatMessage {
  role: "user" | "assistant";
  content: string;
}

export interface QAHistoryItem {
  id: number;
  subject: string;
  question: string;
  answer: string;
  hits_count: number;
  answer_mode: string;
  source_filters: string[];
  warning: string;
  rewritten_query: string;
  hits: Hit[];
  created_at: string;
}

export interface QAHistoryListResponse {
  records: QAHistoryItem[];
  total: number;
}

export interface DeleteQAHistoryResponse {
  success: boolean;
  deleted_id: number;
}

export type LongformType = "analysis" | "study_notes" | "report" | "review" | "outline";

export interface LongformRequest {
  source_filters: string[];
  longform_type: LongformType;
  target_length: number;
  include_sources: boolean;
  strategy: "staged";
  user_instruction?: string;
}

export interface LongformGroupSummary {
  group_index?: number;
  source_label?: string;
  chunks_count?: number;
  chunk_count?: number;
  page_range?: string;
  summary: string;
}

export interface LongformStats {
  total_chunks: number;
  used_chunks: number;
  groups_count: number;
}

export interface LongformResponse {
  content: string;
  outline?: string;
  group_summaries: LongformGroupSummary[];
  sources: Hit[];
  warnings: string[];
  stats: LongformStats;
}
