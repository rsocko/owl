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
    discoveryRun: () => api.post('/api/statements/discovery/run'),
    recommendationsRun: (asOf: string) => api.post(`/api/statements/recommendations/run?as_of=${asOf}`),
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
    unmatched: () => api.get('/api/eob/unmatched'),
    purgeStale: () => api.post('/api/eob/purge-stale'),
    benchmark: (body?: unknown) => api.post('/api/eob/benchmark', body),
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
  },
  documents: {
    preview: (id: string) => `/api/documents/${id}/preview`,
    download: (id: string) => `/api/documents/${id}/download`,
  },
};
