import type {
  BuildIndexRequest,
  BuildIndexResponse,
  AiSettings,
  AiSettingsRequest,
  ConvertPptResponse,
  CreateSubjectResponse,
  DeleteMaterialResponse,
  DeleteQAHistoryResponse,
  ExportDocumentRequest,
  ExportSelfTestRequest,
  LongformRequest,
  LongformResponse,
  Material,
  MaterialStatus,
  OverviewRequest,
  OverviewResponse,
  QAHistoryItem,
  QAHistoryListResponse,
  QARequest,
  QAResponse,
  RenameMaterialResponse,
  SnippetKeywordsRequest,
  SnippetKeywordsResponse,
  StudyGuideRequest,
  StudyGuideResponse,
  Subject,
  SubjectStatus,
  UploadMaterialsResponse
} from "./types";

export const API_BASE_URL = "http://127.0.0.1:8000";

interface ApiMaterial {
  file_name: string;
  relative_path: string;
  file_type: string;
  size_bytes: number;
  build_status?: string | null;
  chapter?: number | string | null;
  group?: string | null;
  category?: string | null;
  conversion_status?: string | null;
  converted_pptx?: string | null;
}

interface ApiSubjectsObject {
  subjects?: unknown;
}

interface ApiMaterialsObject {
  materials?: unknown;
  warning?: string | null;
}

function assertOk(response: Response, endpoint: string): void {
  if (!response.ok) {
    throw new Error(`${endpoint} 请求失败：HTTP ${response.status} ${response.statusText}`);
  }
}

function parseSubjectNames(payload: unknown): string[] {
  const rawSubjects = Array.isArray(payload)
    ? payload
    : (payload as ApiSubjectsObject | null)?.subjects;

  if (!Array.isArray(rawSubjects)) {
    throw new Error("/api/subjects 响应格式不正确，应为数组或 { subjects: string[] }。");
  }

  return rawSubjects.filter((item): item is string => typeof item === "string" && item.trim().length > 0);
}

function parseMaterials(payload: unknown): ApiMaterial[] {
  const rawMaterials = Array.isArray(payload)
    ? payload
    : (payload as ApiMaterialsObject | null)?.materials;

  if (!Array.isArray(rawMaterials)) {
    throw new Error("/api/subjects/{subject}/materials 响应格式不正确，应为数组或 { materials: Material[] }。");
  }

  return rawMaterials as ApiMaterial[];
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

async function fetchJson<T>(
  url: string,
  endpoint: string,
  subject?: string,
  init?: RequestInit
): Promise<T> {
  try {
    const response = await fetch(url, init);
    assertOk(response, endpoint);
    return (await response.json()) as T;
  } catch (error) {
    console.error("FastAPI request failed", {
      url,
      message: errorMessage(error),
      subject,
      API_BASE_URL
    });
    throw new Error(`完整 URL ${url} 请求失败：${errorMessage(error)}`);
  }
}

function formatBytes(size: number): string {
  if (!Number.isFinite(size) || size <= 0) {
    return "0 B";
  }
  const units = ["B", "KB", "MB", "GB"];
  const index = Math.min(Math.floor(Math.log(size) / Math.log(1024)), units.length - 1);
  const value = size / 1024 ** index;
  return `${value >= 10 || index === 0 ? value.toFixed(0) : value.toFixed(1)} ${units[index]}`;
}

function materialStatus(material: ApiMaterial): MaterialStatus {
  if (
    material.build_status === "已建库" ||
    material.build_status === "未建库" ||
    material.build_status === "待转换" ||
    material.build_status === "索引异常"
  ) {
    return material.build_status;
  }
  if (material.build_status === "未知") {
    return "索引异常";
  }
  if (material.conversion_status === "待转换") {
    return "待转换";
  }
  return "未建库";
}

function materialCategory(material: ApiMaterial): string {
  if (material.category) {
    return material.category;
  }
  if (material.chapter !== null && material.chapter !== undefined) {
    return `第${material.chapter}章`;
  }
  return "课程资料";
}

function toMaterial(material: ApiMaterial): Material {
  const relativePath = material.relative_path || material.file_name;
  const category = materialCategory(material);
  return {
    id: relativePath,
    fileName: material.file_name,
    relativePath,
    fileType: material.file_type.replace(".", "").toUpperCase() || "FILE",
    fileExtension: material.file_type.toLowerCase(),
    sizeLabel: formatBytes(material.size_bytes),
    chapter:
      material.chapter !== null && material.chapter !== undefined
        ? `第${material.chapter}章`
        : undefined,
    group: material.group || category,
    status: materialStatus(material),
    category,
    lastUsed: "API",
    conversionStatus: material.conversion_status || "",
    convertedPptx: material.converted_pptx ?? null
  };
}

export async function fetchSubjects(): Promise<Subject[]> {
  const endpoint = "/api/subjects";
  const subjects = parseSubjectNames(await fetchJson<unknown>(`${API_BASE_URL}${endpoint}`, endpoint));
  return subjects.map((name) => ({ name }));
}

export async function createSubject(name: string): Promise<CreateSubjectResponse> {
  const endpoint = "/api/subjects";
  const url = `${API_BASE_URL}${endpoint}`;
  try {
    const response = await fetch(url, {
      method: "POST",
      headers: {
        "Content-Type": "application/json"
      },
      body: JSON.stringify({ name })
    });
    if (!response.ok) {
      let detail = `${endpoint} 请求失败：HTTP ${response.status} ${response.statusText}`;
      try {
        const payload = await response.json();
        if (typeof payload?.detail === "string" && payload.detail.trim()) {
          detail = payload.detail;
        }
      } catch {
        // Keep the HTTP status fallback when the server returns a non-JSON error.
      }
      throw new Error(detail);
    }
    return (await response.json()) as CreateSubjectResponse;
  } catch (error) {
    console.error("FastAPI request failed", {
      url,
      message: errorMessage(error),
      API_BASE_URL
    });
    throw new Error(errorMessage(error));
  }
}

export async function fetchSubjectMaterials(subject: string): Promise<Material[]> {
  const endpoint = `/api/subjects/${encodeURIComponent(subject)}/materials`;
  const url = `${API_BASE_URL}${endpoint}`;
  const materials = parseMaterials(await fetchJson<unknown>(url, endpoint, subject));
  return materials.map(toMaterial);
}

export async function fetchSubjectStatus(subject: string): Promise<SubjectStatus> {
  const endpoint = `/api/subjects/${encodeURIComponent(subject)}/status`;
  const url = `${API_BASE_URL}${endpoint}`;
  return fetchJson<SubjectStatus>(url, endpoint, subject);
}

export async function fetchAiSettings(): Promise<AiSettings> {
  const endpoint = "/api/ai/settings";
  const url = `${API_BASE_URL}${endpoint}`;
  return fetchJson<AiSettings>(url, endpoint);
}

export async function saveAiSettings(payload: AiSettingsRequest): Promise<AiSettings> {
  const endpoint = "/api/ai/settings";
  const url = `${API_BASE_URL}${endpoint}`;
  return fetchJson<AiSettings>(url, endpoint, undefined, {
    method: "POST",
    headers: {
      "Content-Type": "application/json"
    },
    body: JSON.stringify(payload)
  });
}

export async function askQuestion(subject: string, payload: QARequest): Promise<QAResponse> {
  const endpoint = `/api/subjects/${encodeURIComponent(subject)}/qa`;
  const url = `${API_BASE_URL}${endpoint}`;
  return fetchJson<QAResponse>(url, endpoint, subject, {
    method: "POST",
    headers: {
      "Content-Type": "application/json"
    },
    body: JSON.stringify(payload)
  });
}

export async function fetchQaHistory(subject: string, limit = 50, offset = 0): Promise<QAHistoryListResponse> {
  const endpoint = `/api/qa-history?subject=${encodeURIComponent(subject)}&limit=${limit}&offset=${offset}`;
  const url = `${API_BASE_URL}${endpoint}`;
  return fetchJson<QAHistoryListResponse>(url, endpoint, subject);
}

export async function fetchQaHistoryItem(id: number): Promise<QAHistoryItem> {
  const endpoint = `/api/qa-history/${id}`;
  const url = `${API_BASE_URL}${endpoint}`;
  return fetchJson<QAHistoryItem>(url, endpoint);
}

export async function deleteQaHistoryItem(id: number): Promise<DeleteQAHistoryResponse> {
  const endpoint = `/api/qa-history/${id}`;
  const url = `${API_BASE_URL}${endpoint}`;
  return fetchJson<DeleteQAHistoryResponse>(url, endpoint, undefined, {
    method: "DELETE"
  });
}

export async function fetchSnippetKeywords(payload: SnippetKeywordsRequest): Promise<SnippetKeywordsResponse> {
  const endpoint = "/api/preview/snippet-keywords";
  const url = `${API_BASE_URL}${endpoint}`;
  return fetchJson<SnippetKeywordsResponse>(url, endpoint, undefined, {
    method: "POST",
    headers: {
      "Content-Type": "application/json"
    },
    body: JSON.stringify(payload)
  });
}

export async function buildIndex(subject: string, payload: BuildIndexRequest): Promise<BuildIndexResponse> {
  const endpoint = `/api/subjects/${encodeURIComponent(subject)}/index`;
  const url = `${API_BASE_URL}${endpoint}`;
  return fetchJson<BuildIndexResponse>(url, endpoint, subject, {
    method: "POST",
    headers: {
      "Content-Type": "application/json"
    },
    body: JSON.stringify(payload)
  });
}

export async function fetchStudyGuide(
  subject: string,
  payload: StudyGuideRequest
): Promise<StudyGuideResponse> {
  const endpoint = `/api/subjects/${encodeURIComponent(subject)}/study-guide`;
  const url = `${API_BASE_URL}${endpoint}`;
  return fetchJson<StudyGuideResponse>(url, endpoint, subject, {
    method: "POST",
    headers: {
      "Content-Type": "application/json"
    },
    body: JSON.stringify(payload)
  });
}

export async function fetchOverview(
  subject: string,
  payload: OverviewRequest
): Promise<OverviewResponse> {
  const endpoint = `/api/subjects/${encodeURIComponent(subject)}/overview`;
  const url = `${API_BASE_URL}${endpoint}`;
  return fetchJson<OverviewResponse>(url, endpoint, subject, {
    method: "POST",
    headers: {
      "Content-Type": "application/json"
    },
    body: JSON.stringify(payload)
  });
}

export async function fetchLongformAnalysis(
  subject: string,
  payload: LongformRequest
): Promise<LongformResponse> {
  const endpoint = `/api/subjects/${encodeURIComponent(subject)}/longform`;
  const url = `${API_BASE_URL}${endpoint}`;
  return fetchJson<LongformResponse>(url, endpoint, subject, {
    method: "POST",
    headers: {
      "Content-Type": "application/json"
    },
    body: JSON.stringify(payload)
  });
}

export async function uploadMaterials(subject: string, files: File[]): Promise<UploadMaterialsResponse> {
  const endpoint = `/api/subjects/${encodeURIComponent(subject)}/materials/upload`;
  const url = `${API_BASE_URL}${endpoint}`;
  const formData = new FormData();
  files.forEach((file) => formData.append("files", file));
  return fetchJson<UploadMaterialsResponse>(url, endpoint, subject, {
    method: "POST",
    body: formData
  });
}

export async function deleteMaterial(subject: string, relativePath: string): Promise<DeleteMaterialResponse> {
  const endpoint = `/api/subjects/${encodeURIComponent(subject)}/materials/delete`;
  const url = `${API_BASE_URL}${endpoint}`;
  return fetchJson<DeleteMaterialResponse>(url, endpoint, subject, {
    method: "POST",
    headers: {
      "Content-Type": "application/json"
    },
    body: JSON.stringify({
      relative_path: relativePath
    })
  });
}

export async function renameMaterial(
  subject: string,
  oldRelativePath: string,
  newRelativePath: string
): Promise<RenameMaterialResponse> {
  const endpoint = `/api/subjects/${encodeURIComponent(subject)}/materials/rename`;
  const url = `${API_BASE_URL}${endpoint}`;
  return fetchJson<RenameMaterialResponse>(url, endpoint, subject, {
    method: "POST",
    headers: {
      "Content-Type": "application/json"
    },
    body: JSON.stringify({
      old_relative_path: oldRelativePath,
      new_relative_path: newRelativePath
    })
  });
}

export async function exportSelfTestDocx(payload: ExportSelfTestRequest): Promise<void> {
  const endpoint = "/api/export/self-test/docx";
  const url = `${API_BASE_URL}${endpoint}`;
  try {
    const response = await fetch(url, {
      method: "POST",
      headers: {
        "Content-Type": "application/json"
      },
      body: JSON.stringify(payload)
    });
    assertOk(response, endpoint);
    const blob = await response.blob();
    const downloadUrl = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = downloadUrl;
    link.download = payload.filename || `自测题-${payload.subject}.docx`;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    URL.revokeObjectURL(downloadUrl);
  } catch (error) {
    console.error("POST /api/export/self-test/docx failed", error);
    throw new Error(`自测题 Word 导出失败：${errorMessage(error)}`);
  }
}

export async function exportDocumentDocx(payload: ExportDocumentRequest): Promise<void> {
  const endpoint = "/api/export/document/docx";
  const url = `${API_BASE_URL}${endpoint}`;
  try {
    const response = await fetch(url, {
      method: "POST",
      headers: {
        "Content-Type": "application/json"
      },
      body: JSON.stringify(payload)
    });
    assertOk(response, endpoint);
    const blob = await response.blob();
    const downloadUrl = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = downloadUrl;
    link.download = payload.filename || `${payload.filename_prefix}-${payload.subject}.docx`;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    URL.revokeObjectURL(downloadUrl);
  } catch (error) {
    console.error("POST /api/export/document/docx failed", error);
    throw new Error(`Word 导出失败：${errorMessage(error)}`);
  }
}

export async function convertPptMaterial(
  subject: string,
  relativePath: string
): Promise<ConvertPptResponse> {
  const endpoint = `/api/subjects/${encodeURIComponent(subject)}/materials/convert-ppt`;
  const url = `${API_BASE_URL}${endpoint}`;
  return fetchJson<ConvertPptResponse>(url, endpoint, subject, {
    method: "POST",
    headers: {
      "Content-Type": "application/json"
    },
    body: JSON.stringify({
      relative_path: relativePath
    })
  });
}
