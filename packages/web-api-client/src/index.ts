import type { components } from "./schema";
export type Overview = components["schemas"]["Overview"];
export type ModelRecord = components["schemas"]["ModelRecord"];
export type ModelCreate = components["schemas"]["ModelCreate"];
export type ModelUpdate = components["schemas"]["ModelUpdate"];
export type ModelVersion = components["schemas"]["ModelVersion"];
export type ModelCheck = components["schemas"]["ModelCheck"];
export type Content = components["schemas"]["CaseContent"];
export type Catalog = components["schemas"]["CatalogRecord"];
export type CatalogCreate = components["schemas"]["CatalogCreate"];
export type CatalogWrite = components["schemas"]["CatalogWrite"];
export type CaseSearchPage = components["schemas"]["CaseSearchPage"];
export type CaseListRecord = components["schemas"]["CaseListRecord"];
export type TaskDetail = components["schemas"]["TaskDetail"];
export type TaskRecord = components["schemas"]["TaskRecord"];
export type TaskPage = components["schemas"]["TaskPage"];
export type TaskCase = components["schemas"]["TaskCase"];
export type TaskConfiguration = components["schemas"]["TaskConfiguration"];
export type TaskAttempt = components["schemas"]["TaskAttempt"];
export type ExecutionReport = components["schemas"]["ExecutionReport"];
export type BatchDetail = components["schemas"]["BatchDetail"];
export type BatchReport = components["schemas"]["BatchReport"];
export type ReportPage = components["schemas"]["ReportPage"];
export type ExecutionBatch = components["schemas"]["ExecutionBatch"];
export type ImportUpload = components["schemas"]["ImportUpload"];
export type ImportBatch = components["schemas"]["ImportBatch"];
export type ImportRow = components["schemas"]["ImportRow"];
export type ImportPreviewRequest =
  components["schemas"]["ImportPreviewRequest"];
export type ImportHistory = Pick<
  ImportBatch,
  "id" | "state" | "source" | "source_identity" | "result" | "created_at"
>;
async function fetchApi(
  path: string,
  method = "GET",
  body?: unknown,
  signal?: AbortSignal,
): Promise<Response> {
  const response = await fetch(`/api${path}`, {
    method,
    headers: {
      "Content-Type":
        body instanceof Blob ? "application/octet-stream" : "application/json",
    },
    body:
      body instanceof Blob
        ? body
        : body === undefined
          ? undefined
          : JSON.stringify(body),
    signal,
  });
  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    throw new Error(
      Array.isArray(error.detail)
        ? error.detail.map((item: { msg: string }) => item.msg).join("；")
        : error.detail || error.error || `请求失败 ${response.status}`,
    );
  }
  return response;
}

export async function request<T>(
  path: string,
  method = "GET",
  body?: unknown,
  signal?: AbortSignal,
): Promise<T> {
  return (await fetchApi(path, method, body, signal)).json();
}

export const api = {
  models: () => request<ModelRecord[]>("/models"),
  createModel: (body: ModelCreate) =>
    request<ModelRecord>("/models", "POST", body),
  updateModel: (id: string, body: ModelUpdate) =>
    request<ModelRecord>(`/models/${id}`, "PUT", body),
  modelVersions: (id: string) =>
    request<ModelVersion[]>(`/models/${id}/versions`),
  modelAvailability: (id: string, revision: number, enabled: boolean) =>
    request<ModelRecord>(`/models/${id}/availability`, "PUT", {
      expected_revision: revision,
      enabled,
    }),
  checkModel: (id: string, revision: number) =>
    request<ModelCheck>(`/models/${id}/check`, "POST", {
      expected_revision: revision,
    }),
  tasks: (query: string, signal?: AbortSignal) =>
    request<TaskPage>(`/tasks?${query}`, "GET", undefined, signal),
  task: (id: string) => request<TaskDetail>(`/tasks/${id}`),
  createTask: (body: components["schemas"]["CreateTask"]) =>
    request<TaskDetail>("/tasks", "POST", body),
  saveTask: (id: string, body: components["schemas"]["SaveTask"]) =>
    request<TaskDetail>(`/tasks/${id}`, "PUT", body),
  executeTask: (id: string, requestId: string, expectedRevision: number) =>
    request<BatchDetail>(`/tasks/${id}/execute`, "POST", {
      request_id: requestId,
      expected_revision: expectedRevision,
    }),
  executions: (taskId?: string, signal?: AbortSignal) =>
    request<ExecutionBatch[]>(
      `/executions${taskId ? `?task_id=${encodeURIComponent(taskId)}` : ""}`,
      "GET",
      undefined,
      signal,
    ),
  execution: (id: string) => request<BatchDetail>(`/executions/${id}`),
  reports: (query: string, signal?: AbortSignal) =>
    request<ReportPage>(`/reports?${query}`, "GET", undefined, signal),
  report: (id: string, signal?: AbortSignal) =>
    request<BatchReport>(`/reports/${id}`, "GET", undefined, signal),
  exportReport: (id: string, format: "html" | "json") =>
    fetchApi(
      `/reports/${encodeURIComponent(id)}/export?format=${format}`,
      "POST",
    ),
  executionReport: (attemptId: string, signal?: AbortSignal) =>
    request<ExecutionReport | null>(
      `/attempts/${attemptId}/report`,
      "GET",
      undefined,
      signal,
    ),
  executionFrameUrl: (attemptId: string, digest: string) =>
    `/api/attempts/${encodeURIComponent(attemptId)}/report/frames/${encodeURIComponent(digest)}`,
  cancelExecution: (id: string) =>
    request<BatchDetail>(`/executions/${id}/cancel`, "POST"),
  overview: () => request<Overview>("/overview"),
  createCase: (name: string, code?: string) =>
    request<{ id: string }>("/cases", "POST", { name, code }),
  searchCases: (query: string, signal?: AbortSignal) =>
    request<CaseSearchPage>(`/cases?${query}`, "GET", undefined, signal),
  createCatalog: (body: CatalogCreate) =>
    request<Catalog>("/catalogs", "POST", body),
  updateCatalog: (id: string, body: CatalogWrite) =>
    request<Catalog>(`/catalogs/${id}`, "PUT", body),
  deleteCatalog: (id: string) =>
    request<{ deleted: boolean }>(`/catalogs/${id}`, "DELETE"),
  addVersion: (id: string, content: Content, expectedLatest?: string | null) =>
    request<{ id: string }>(
      `/cases/${id}/versions${expectedLatest === undefined ? "" : `?expected_latest=${expectedLatest ?? "none"}`}`,
      "POST",
      content,
    ),
  setStable: (
    caseId: string,
    versionId: string,
    expectedVersionId: string | null,
  ) =>
    request(`/cases/${caseId}/stable`, "PUT", {
      version_id: versionId,
      expected_version_id: expectedVersionId,
    }),
  uploadExcel: (file: File) =>
    request<ImportUpload>(
      `/imports/excel?filename=${encodeURIComponent(file.name)}`,
      "POST",
      file,
    ),
  importUpload: (id: string) => request<ImportUpload>(`/imports/uploads/${id}`),
  previewImport: (body: ImportPreviewRequest) =>
    request<ImportBatch>("/imports/preview", "POST", body),
  importBatch: (id: string) => request<ImportBatch>(`/imports/${id}`),
  importHistory: () => request<ImportHistory[]>("/imports"),
  acceptImport: (
    id: string,
    rows: number[],
    acknowledged_conflicts: number[],
  ) =>
    request<ImportBatch>(`/imports/${id}/accept`, "POST", {
      rows,
      acknowledged_conflicts,
    }),
  run: (caseId: string) =>
    request<{ id: string }>("/runs", "POST", {
      case_id: caseId,
      request_id: crypto.randomUUID(),
    }),
  cancel: (id: string) => request(`/attempts/${id}/cancel`, "POST"),
  retry: (id: string) => request(`/attempts/${id}/retry`, "POST"),
};
