const BACKEND_URL = process.env.NEXT_PUBLIC_BACKEND_URL || "http://localhost:8000";

export class ApiError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
}

async function request<T>(
  path: string,
  token: string,
  init?: RequestInit
): Promise<T> {
  const res = await fetch(`${BACKEND_URL}${path}`, {
    ...init,
    headers: {
      Authorization: `Bearer ${token}`,
      ...(init?.body && !(init.body instanceof FormData) ? { "Content-Type": "application/json" } : {}),
      ...init?.headers,
    },
  });

  if (!res.ok) {
    let message = `Request failed with status ${res.status}`;
    try {
      const data = await res.json();
      message = data.detail || data.error || message;
    } catch {
      // ignore body parse errors
    }
    throw new ApiError(message, res.status);
  }

  if (res.status === 204) return undefined as T;
  const contentType = res.headers.get("content-type") || "";
  if (contentType.includes("application/json")) {
    return res.json();
  }
  return res.blob() as unknown as T;
}

// ─────────────────────────── Types ───────────────────────────

export interface Project {
  id: string;
  name: string;
  description: string | null;
  mode: "classification" | "generation" | "both";
  total_companies: number;
  created_at: string;
  updated_at: string;
  latest_job_status: string | null;
}

export interface Job {
  id: string;
  project_id: string;
  status: "PENDING" | "QUEUED" | "RUNNING" | "PAUSED" | "COMPLETED" | "FAILED" | "CANCELLED";
  mode: string;
  concurrency: number;
  total: number;
  done: number;
  failed: number;
  skipped: number;
  error_message: string | null;
  started_at: string | null;
  finished_at: string | null;
  created_at: string;
}

export interface UploadCsvResult {
  total_rows: number;
  preview: Record<string, string>[];
  errors: { row: number; message: string }[];
  detected_columns: { company_name: string | null; url: string | null; location: string | null };
}

export interface Company {
  id: string;
  row_index: number;
  company_name: string;
  location: string | null;
  url: string | null;
  status: "pending" | "running" | "done" | "failed";
  url_read_source: "jina_reader" | "compound_beta" | "name_location" | "none" | null;
  url_read_success: boolean | null;
  url_error: string | null;
  supply_chain_primary: string | null;
  display_label: string | null;
  classification_confidence: number | null;
  is_multi: boolean | null;
  products_count: number;
  processing_time_ms: number | null;
  error_message: string | null;
}

export interface CompanyPage {
  companies: Company[];
  next_cursor: string | null;
  total: number;
}

export interface ApiKey {
  id: string;
  provider: string;
  label: string;
  masked_key: string;
  model_name: string | null;
  base_url: string | null;
  is_active: boolean;
  created_at: string;
}

export interface ModelUsage {
  tag: string;
  requests_today: number;
  tokens_today: number;
  cached_tokens_today: number;
  requests_month: number;
  tokens_month: number;
  limit_requests_per_day: number | null;
  limit_tokens_per_day: number | null;
  remaining_requests_today: number | null;
  remaining_tokens_today: number | null;
  live_remaining_tokens: number | null;
  live_limit_tokens: number | null;
  live_reset_tokens_s: number | null;
  last_used_at: string | null;
}

export interface KeyUsage {
  key_ref: string;
  provider: string;
  label: string;
  masked_key: string;
  is_system: boolean;
  requests_today: number;
  tokens_today: number;
  requests_month: number;
  tokens_month: number;
  last_used_at: string | null;
  models: ModelUsage[];
}

export interface ProviderUsageSummary {
  provider: string;
  key_count: number;
  requests_today: number;
  tokens_today: number;
  requests_month: number;
  tokens_month: number;
}

// ─────────────────────────── Email finder ───────────────────────────

export interface EmailBatch {
  id: string;
  name: string;
  status: "PENDING" | "QUEUED" | "RUNNING" | "PAUSED" | "COMPLETED" | "FAILED" | "CANCELLED";
  concurrency: number;
  total: number;
  done: number;
  failed: number;
  error_message: string | null;
  started_at: string | null;
  finished_at: string | null;
  created_at: string;
}

export type EmailTier =
  | "scraped_verified"
  | "scraped_offsite"
  | "pattern_smtp_verified"
  | "pattern_catchall"
  | "pattern_unverified";

export type WebsiteSource = "provided" | "web_search" | "domain_guess" | "not_found";

export interface EmailCandidateOut {
  email: string;
  label: string;
  tier: EmailTier;
  confidence: number;
  source_page: string | null;
}

export interface EmailCompany {
  id: string;
  row_index: number;
  company_name: string;
  location: string | null;
  url: string | null;
  status: "pending" | "running" | "done" | "failed";
  resolved_url: string | null;
  website_source: WebsiteSource | null;
  primary_email: string | null;
  primary_label: string | null;
  primary_tier: EmailTier | null;
  primary_confidence: number | null;
  primary_source_page: string | null;
  alternate_emails: EmailCandidateOut[];
  processing_time_ms: number | null;
  error_message: string | null;
}

export interface EmailCompanyPage {
  companies: EmailCompany[];
  next_cursor: string | null;
  total: number;
}

export interface EmailBatchStats {
  total_companies: number;
  with_email: number;
  by_tier: Record<string, number>;
  by_website_source: Record<string, number>;
}

// ─────────────────────────── Projects ───────────────────────────

export const api = {
  listProjects: (token: string) => request<Project[]>("/api/projects", token),

  createProject: (token: string, body: { name: string; description?: string; mode: string }) =>
    request<Project>("/api/projects", token, { method: "POST", body: JSON.stringify(body) }),

  getProject: (token: string, id: string) => request<Project>(`/api/projects/${id}`, token),

  deleteProject: (token: string, id: string) =>
    request<{ status: string }>(`/api/projects/${id}`, token, { method: "DELETE" }),

  uploadCsv: (token: string, projectId: string, file: File) => {
    const form = new FormData();
    form.append("file", file);
    return request<UploadCsvResult>(`/api/projects/${projectId}/upload-csv`, token, {
      method: "POST",
      body: form,
    });
  },

  // ─────────────────────────── Jobs ───────────────────────────

  createJob: (token: string, body: { project_id: string; mode?: string; concurrency?: number }) =>
    request<Job>("/api/jobs", token, { method: "POST", body: JSON.stringify(body) }),

  getJob: (token: string, id: string) => request<Job>(`/api/jobs/${id}`, token),

  listJobsForProject: (token: string, projectId: string) =>
    request<Job[]>(`/api/jobs/project/${projectId}`, token),

  pauseJob: (token: string, id: string) =>
    request<{ status: string }>(`/api/jobs/${id}/pause`, token, { method: "POST" }),

  resumeJob: (token: string, id: string) =>
    request<{ status: string }>(`/api/jobs/${id}/resume`, token, { method: "POST" }),

  cancelJob: (token: string, id: string) =>
    request<{ status: string }>(`/api/jobs/${id}/cancel`, token, { method: "POST" }),

  // Returns the brand new Job created to reprocess just the failed
  // companies (never the same job_id - see backend docstring for why).
  retryFailed: (token: string, id: string) =>
    request<Job>(`/api/jobs/${id}/retry-failed`, token, { method: "POST" }),

  // ─────────────────────────── Companies ───────────────────────────

  listCompanies: (
    token: string,
    params: { projectId: string; cursor?: string; limit?: number; status?: string }
  ) => {
    const search = new URLSearchParams({ project_id: params.projectId });
    if (params.cursor) search.set("cursor", params.cursor);
    if (params.limit) search.set("limit", String(params.limit));
    if (params.status) search.set("status", params.status);
    return request<CompanyPage>(`/api/companies?${search.toString()}`, token);
  },

  getCompanyProducts: (token: string, companyId: string) =>
    request<{ company_id: string; products: Record<string, string>[] }>(
      `/api/companies/${companyId}/products`,
      token
    ),

  // ─────────────────────────── Export ───────────────────────────

  exportUrl: (projectId: string, fmt: "csv" | "json", token: string, jobId?: string) => {
    const search = new URLSearchParams({ fmt, token });
    if (jobId) search.set("job_id", jobId);
    return `${BACKEND_URL}/api/export/${projectId}?${search.toString()}`;
  },

  getStats: (token: string, projectId: string, jobId?: string) => {
    const search = jobId ? `?job_id=${jobId}` : "";
    return request<{ total_companies: number; total_products: number; by_category: Record<string, number> }>(
      `/api/export/${projectId}/stats${search}`,
      token
    );
  },

  // ─────────────────────────── Settings ───────────────────────────

  listApiKeys: (token: string) => request<ApiKey[]>("/api/settings/keys", token),

  addApiKey: (
    token: string,
    body: { provider: string; label: string; api_key: string; model_name?: string; base_url?: string }
  ) => request<ApiKey>("/api/settings/keys", token, { method: "POST", body: JSON.stringify(body) }),

  /**
   * Accepts one label plus one-or-more comma/newline-separated keys.
   * A single key keeps the label as typed; multiple keys are auto-numbered
   * "<label> 1", "<label> 2", ... server-side.
   */
  addApiKeysBulk: (
    token: string,
    body: { provider: string; label: string; api_keys: string; model_name?: string; base_url?: string }
  ) => request<ApiKey[]>("/api/settings/keys/bulk", token, { method: "POST", body: JSON.stringify(body) }),

  toggleApiKey: (token: string, id: string) =>
    request<{ id: string; is_active: boolean }>(`/api/settings/keys/${id}/toggle`, token, { method: "PATCH" }),

  deleteApiKey: (token: string, id: string) =>
    request<{ status: string }>(`/api/settings/keys/${id}`, token, { method: "DELETE" }),

  getKeyUsage: (token: string) => request<KeyUsage[]>("/api/settings/usage", token),

  getUsageSummary: (token: string) => request<ProviderUsageSummary[]>("/api/settings/usage/summary", token),

  streamUrl: (jobId: string, token: string) => `${BACKEND_URL}/api/stream/${jobId}?token=${encodeURIComponent(token)}`,

  getCircuitStatus: (token: string) =>
    request<{ state: "CLOSED" | "OPEN" | "HALF_OPEN"; failures: number; reset_in_s: number }>(
      "/api/jobs/circuit-status/jina",
      token
    ),

  // ─────────────────────────── Email finder ───────────────────────────
  // Independent feature/section (see Dashboard sidebar "Email Finder") -
  // its own batches, not tied to Projects/Jobs at all. Reuses the same
  // `request()` helper, error handling, and SSE stream endpoint (streamUrl
  // above is already generic over any id string) as everything else here.

  listEmailBatches: (token: string) => request<EmailBatch[]>("/api/email-finder/batches", token),

  createEmailBatch: (token: string, body: { name: string }) =>
    request<EmailBatch>("/api/email-finder/batches", token, { method: "POST", body: JSON.stringify(body) }),

  getEmailBatch: (token: string, id: string) => request<EmailBatch>(`/api/email-finder/batches/${id}`, token),

  deleteEmailBatch: (token: string, id: string) =>
    request<{ status: string }>(`/api/email-finder/batches/${id}`, token, { method: "DELETE" }),

  uploadEmailCsv: (token: string, batchId: string, file: File) => {
    const form = new FormData();
    form.append("file", file);
    return request<UploadCsvResult>(`/api/email-finder/batches/${batchId}/upload-csv`, token, {
      method: "POST",
      body: form,
    });
  },

  startEmailBatch: (token: string, batchId: string, concurrency?: number) =>
    request<EmailBatch>(`/api/email-finder/batches/${batchId}/start`, token, {
      method: "POST",
      body: JSON.stringify({ concurrency: concurrency ?? 10 }),
    }),

  pauseEmailBatch: (token: string, batchId: string) =>
    request<{ status: string }>(`/api/email-finder/batches/${batchId}/pause`, token, { method: "POST" }),

  resumeEmailBatch: (token: string, batchId: string) =>
    request<EmailBatch>(`/api/email-finder/batches/${batchId}/resume`, token, { method: "POST" }),

  cancelEmailBatch: (token: string, batchId: string) =>
    request<{ status: string }>(`/api/email-finder/batches/${batchId}/cancel`, token, { method: "POST" }),

  retryFailedEmailCompanies: (token: string, batchId: string) =>
    request<EmailBatch>(`/api/email-finder/batches/${batchId}/retry-failed`, token, { method: "POST" }),

  listEmailCompanies: (
    token: string,
    params: { batchId: string; cursor?: string; limit?: number; status?: string }
  ) => {
    const search = new URLSearchParams();
    if (params.cursor) search.set("cursor", params.cursor);
    if (params.limit) search.set("limit", String(params.limit));
    if (params.status) search.set("status", params.status);
    const qs = search.toString();
    return request<EmailCompanyPage>(
      `/api/email-finder/batches/${params.batchId}/companies${qs ? `?${qs}` : ""}`,
      token
    );
  },

  getEmailBatchStats: (token: string, batchId: string) =>
    request<EmailBatchStats>(`/api/email-finder/batches/${batchId}/stats`, token),

  exportEmailBatchUrl: (batchId: string, fmt: "csv" | "json", token: string) => {
    const search = new URLSearchParams({ fmt, token });
    return `${BACKEND_URL}/api/email-finder/batches/${batchId}/export?${search.toString()}`;
  },
};

export { BACKEND_URL };
