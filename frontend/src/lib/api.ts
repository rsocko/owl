/**
 * Lightweight API client for the Document Intelligence Hub FastAPI backend.
 * In dev, Vite proxies /api and /health to http://localhost:8001 (see vite.config.ts).
 * In production the built frontend is served by the same FastAPI app, so relative
 * paths just work.
 */

export class ApiError extends Error {
  status: number;
  details?: unknown;
  constructor(message: string, status: number, details?: unknown) {
    super(message);
    this.status = status;
    this.details = details;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    headers: { 'Content-Type': 'application/json', ...(init?.headers || {}) },
    ...init,
  });
  if (!res.ok) {
    let details: unknown;
    try {
      details = await res.json();
    } catch {
      details = undefined;
    }
    const message =
      (details as { error?: { message?: string } } | undefined)?.error?.message ||
      `Request to ${path} failed with ${res.status}`;
    throw new ApiError(message, res.status, details);
  }
  if (res.status === 204) return undefined as T;
  const text = await res.text();
  return (text ? JSON.parse(text) : undefined) as T;
}

export const api = {
  get: <T,>(path: string) => request<T>(path),
  post: <T,>(path: string, body?: unknown) =>
    request<T>(path, { method: 'POST', body: body !== undefined ? JSON.stringify(body) : undefined }),
  patch: <T,>(path: string, body?: unknown) =>
    request<T>(path, { method: 'PATCH', body: body !== undefined ? JSON.stringify(body) : undefined }),
  put: <T,>(path: string, body?: unknown) =>
    request<T>(path, { method: 'PUT', body: body !== undefined ? JSON.stringify(body) : undefined }),
  delete: <T,>(path: string) => request<T>(path, { method: 'DELETE' }),
};

// ---- Known endpoint helpers (see backend routers under api/routers/) ----
export const endpoints = {
  health: () => api.get('/health'),
  status: () => api.get('/api/status'),
  stats: () => api.get('/api/stats'),
  paperlessHealth: () => api.get('/api/paperless/health'),
  settings: {
    get: () => api.get('/api/settings'),
    update: (body: unknown) => api.put('/api/settings', body),
  },
  llm: {
    test: (body: unknown) => api.post('/api/llm/test', body),
    models: () => api.get('/api/llm/models'),
  },
  statements: {
    missing: () => api.get('/api/statements/missing'),
    providers: () => api.get('/api/statements/providers'),
    discoveryRun: () => api.post('/api/statements/discovery/run'),
    discoveryStreamUrl: '/api/statements/discovery/stream',
    recommendationsRun: (asOf: string) => api.post(`/api/statements/recommendations/run?as_of=${asOf}`),
    recommendationsStreamUrl: (asOf: string) => `/api/statements/recommendations/stream?as_of=${asOf}`,
    providerOverrides: () => api.get('/api/statements/providers/overrides'),
    setProviderOverride: (key: string, body: unknown) => api.post(`/api/statements/providers/${key}/override`, body),
    clearProviderOverride: (key: string) => api.delete(`/api/statements/providers/${key}/override`),
    documentPreview: (docId: string) => `/api/statements/documents/${docId}/preview`,
    documentThumb: (docId: string) => `/api/statements/documents/${docId}/thumb`,
  },
  eob: {
    check: () => api.get('/api/eob/check'),
    classify: (body: unknown) => api.post('/api/eob/classify', body),
    run: (body?: unknown) => api.post('/api/eob/run', body),
    results: () => api.get('/api/eob/results'),
    runs: () => api.get('/api/eob/runs'),
    matches: (params?: string) => api.get(`/api/eob/matches${params ? `?${params}` : ''}`),
    updateMatch: (id: string, body: unknown) => api.patch(`/api/eob/matches/${id}`, body),
    matchHistory: (id: string) => api.get(`/api/eob/matches/${id}/history`),
    matchDetail: (matchId: string) => api.get(`/api/eob/matches/${matchId}/detail`),
    recordDetail: (docId: string) => api.get(`/api/eob/records/${docId}`),
    unmatched: () => api.get('/api/eob/unmatched'),
    bulkUpdate: (body: { ids: string[]; action: 'mark_orphan' | 'mark_paid' }) =>
      api.post('/api/eob/bulk-update', body),
    purgeStale: () => api.post('/api/eob/purge-stale'),
    benchmark: (body?: unknown) => api.post('/api/eob/benchmark', body),
    payMatch: (matchId: string, body: { amount: number; paid_date?: string | null; method?: string | null; notes?: string | null }) =>
      api.post(`/api/eob/matches/${matchId}/pay`, body),
    matchPayments: (matchId: string) => api.get(`/api/eob/matches/${matchId}/payments`),
    paymentSummary: () => api.get('/api/eob/payments/summary'),
  },
  actionQueue: {
    check: () => api.get('/api/queue/check'),
    checkCustomFields: () => api.get('/api/queue/check/custom-fields'),
    run: (body?: unknown) => api.post('/api/queue/run', body),
    status: () => api.get('/api/queue/status'),
    actions: (params?: string) => api.get(`/api/queue/actions${params ? `?${params}` : ''}`),
    updateAction: (id: string, body: unknown) => api.patch(`/api/queue/actions/${id}`, body),
  },
  alerts: {
    list: (params?: string) => api.get(`/api/insights/alerts${params ? `?${params}` : ''}`),
    summary: () => api.get('/api/insights/alerts/summary'),
    acknowledge: (id: string) => api.patch(`/api/insights/alerts/${id}/acknowledge`, undefined),
    resolve: (id: string) => api.patch(`/api/insights/alerts/${id}/resolve`, undefined),
    cleanup: () => api.post('/api/insights/alerts/cleanup'),
  },
  admin: {
    weights: {
      get: () => api.get('/api/admin/weights'),
      update: (body: unknown) => api.put('/api/admin/weights', body),
    },
    schedules: {
      get: () => api.get('/api/admin/schedules'),
      update: (body: unknown) => api.put('/api/admin/schedules', body),
    },
    retention: {
      get: () => api.get('/api/admin/retention'),
      update: (body: unknown) => api.put('/api/admin/retention', body),
    },
    cleanup: (body: unknown) => api.post('/api/admin/cleanup', body),
    storage: () => api.get('/api/admin/storage'),
    documentTypes: () => api.get('/api/admin/document-types'),
    documentTypeMapping: {
      get: () => api.get('/api/admin/document-type-mapping'),
      update: (body: unknown) => api.put('/api/admin/document-type-mapping', body),
    },
  },
  documents: {
    preview: (id: string) => `/api/documents/${id}/metadata`,
    download: (id: string) => `/api/documents/${id}/download`,
  },
  triage: {
    queue: (params?: string) => api.get(`/api/triage/queue${params ? `?${params}` : ''}`),
    item: (id: string) => api.get(`/api/triage/queue/${id}`),
    resolve: (id: string, body: { action: string; payload?: unknown }) =>
      api.post(`/api/triage/queue/${id}/resolve`, body),
    defer: (id: string, body?: { until?: string }) =>
      api.post(`/api/triage/queue/${id}/defer`, body),
    dismiss: (id: string) => api.post(`/api/triage/queue/${id}/dismiss`),
    undo: (id: string) => api.post(`/api/triage/queue/${id}/undo`),
    bulk: (body: { action: string; item_ids: string[]; payload?: unknown }) =>
      api.post<{ affected: number }>('/api/triage/queue/bulk', body),
    bulkConfirmThreshold: (body: { min_confidence: number }) =>
      api.post<{ affected: number }>('/api/triage/queue/bulk-confirm-threshold', body),
    stats: () => api.get('/api/triage/stats'),
    populate: () => api.post('/api/triage/queue/populate'),
  },
  metadata: {
    get: (docId: string | number) => api.get(`/api/metadata/${docId}`),
    correct: (docId: string | number, body: { field_name: string; corrected_value: string; original_value?: string; confidence?: number; source_region?: unknown; notes?: string }) =>
      api.post(`/api/metadata/${docId}/correct`, body),
    confirm: (docId: string | number, body: { field_name: string; current_value?: string; confidence?: number; source_region?: unknown }) =>
      api.post(`/api/metadata/${docId}/confirm`, body),
    writeback: (docId: string | number) => api.post(`/api/metadata/${docId}/writeback`),
    corrections: (params?: string) => api.get(`/api/metadata/corrections${params ? `?${params}` : ''}`),
  },
};
