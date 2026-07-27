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
      (details as { detail?: { message?: string } } | undefined)?.detail?.message ||
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
    // Series grouping
    series: (params?: string) => api.get(`/api/statements/series${params ? `?${params}` : ''}`),
    seriesDetail: (id: string) => api.get(`/api/statements/series/${id}`),
    seriesTimeline: (id: string) => api.get(`/api/statements/series/${id}/timeline`),
    seriesSplit: (id: string, body: { document_ids: string[]; new_series_name: string; account_identifier?: string }) =>
      api.post(`/api/statements/series/${id}/split`, body),
    seriesMerge: (body: { source_series_id: string; target_series_id: string }) =>
      api.post('/api/statements/series/merge', body),
    seriesReassign: (id: string, body: { document_id: string; target_series_id: string }) =>
      api.post(`/api/statements/series/${id}/reassign`, body),
    seriesRename: (id: string, body: { name?: string; account_identifier?: string }) =>
      api.post(`/api/statements/series/${id}/rename`, body),
  },
  eob: {
    check: () => api.get('/api/eob/check'),
    classify: (body: unknown) => api.post('/api/eob/classify', body),
    run: (body?: unknown) => api.post('/api/eob/run', body),
    results: (detailed?: boolean) => api.get(`/api/eob/results${detailed ? '?detailed=true' : ''}`),
    runs: () => api.get('/api/eob/runs'),
    matches: (params?: string) => api.get(`/api/eob/matches${params ? `?${params}` : ''}`),
    updateMatch: (id: string, body: unknown) => api.patch(`/api/eob/matches/${id}`, body),
    matchHistory: (id: string) => api.get(`/api/eob/matches/${id}/history`),
    getMatch: (id: string) => api.get(`/api/eob/matches/${id}`),
    confirmMatch: (id: string, body?: unknown) => api.post(`/api/eob/matches/${id}/confirm`, body),
    rejectMatch: (id: string, body?: unknown) => api.post(`/api/eob/matches/${id}/reject`, body),
    manualMatch: (body: unknown) => api.post('/api/eob/matches/manual', body),
    candidates: (docId: string, params?: string) =>
      api.get(`/api/eob/candidates/${docId}${params ? `?${params}` : ''}`),
    matchDetail: (matchId: string) => api.get(`/api/eob/matches/${matchId}/detail`),
    recordDetail: (docId: string) => api.get(`/api/eob/records/${docId}`),
    unmatched: () => api.get('/api/eob/unmatched'),
    bulkUpdate: (body: { ids: string[]; action: 'mark_orphan' | 'mark_paid' }) =>
      api.post('/api/eob/bulk-update', body),
    purgeStale: () => api.post('/api/eob/purge-stale'),
    benchmark: (body?: unknown) => api.post('/api/eob/benchmark', body),
    benchmarkHistory: (params?: string) => api.get(`/api/eob/benchmark/history${params ? `?${params}` : ''}`),
    benchmarkRunDetail: (runId: number) => api.get(`/api/eob/benchmark/history/${runId}`),
    benchmarkTrends: (limit?: number) => api.get(`/api/eob/benchmark/trends${limit ? `?limit=${limit}` : ''}`),
    payMatch: (matchId: string, body: { amount: number; paid_date?: string | null; method?: string | null; notes?: string | null }) =>
      api.post(`/api/eob/matches/${matchId}/pay`, body),
    matchPayments: (matchId: string) => api.get(`/api/eob/matches/${matchId}/payments`),
    paymentSummary: () => api.get('/api/eob/payments/summary'),
    coverage: (groupBy?: string) =>
      api.get(`/api/eob/coverage${groupBy ? `?group_by=${groupBy}` : ''}`),
  },
  actionQueue: {
    check: () => api.get('/api/queue/check'),
    checkCustomFields: () => api.get('/api/queue/check/custom-fields'),
    run: (body?: unknown) => api.post('/api/queue/run', body),
    status: () => api.get('/api/queue/status'),
    actions: (params?: string) => api.get(`/api/queue/actions${params ? `?${params}` : ''}`),
    updateAction: (id: string, body: unknown) => api.patch(`/api/queue/actions/${id}`, body),
    bulk: (body: { action: string; action_ids: number[] }) =>
      api.post<{ affected: number; action: string }>('/api/queue/actions/bulk', body),
    backfill: (body?: unknown) => api.post('/api/queue/actions/backfill', body),
    settings: () => api.get('/api/queue/settings'),
    updateSettings: (body: unknown) => api.put('/api/queue/settings', body),
    metadataTags: () => api.get('/api/queue/metadata/tags'),
    metadataSavedViews: () => api.get('/api/queue/metadata/saved-views'),
    metadataCorrespondents: () => api.get('/api/queue/metadata/correspondents'),
    metadataDocumentTypes: () => api.get('/api/queue/metadata/document-types'),
  },
  alerts: {
    list: (params?: string) => api.get(`/api/insights/alerts${params ? `?${params}` : ''}`),
    summary: () => api.get('/api/insights/alerts/summary'),
    acknowledge: (id: string) => api.patch(`/api/insights/alerts/${id}/acknowledge`, undefined),
    resolve: (id: string) => api.patch(`/api/insights/alerts/${id}/resolve`, undefined),
    cleanup: () => api.post('/api/insights/alerts/cleanup'),
  },
  insights: {
    list: (params?: string) => api.get(`/api/insights${params ? `?${params}` : ''}`),
    get: (id: string) => api.get(`/api/insights/${id}`),
    acknowledge: (id: string) => api.post(`/api/insights/${id}/acknowledge`),
    archive: (id: string) => api.post(`/api/insights/${id}/archive`),
    bulk: (body: { action: 'acknowledge' | 'archive'; ids: string[] }) =>
      api.post('/api/insights/bulk', body),
    summary: () => api.get('/api/insights/summary'),
    history: (seriesId: string) => api.get(`/api/insights/history/${seriesId}`),
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
  rules: {
    list: () => api.get('/api/rules'),
    get: (id: string) => api.get(`/api/rules/${id}`),
    create: (body: unknown) => api.post('/api/rules', body),
    update: (id: string, body: unknown) => api.put(`/api/rules/${id}`, body),
    delete: (id: string) => api.delete(`/api/rules/${id}`),
    toggle: (id: string, body: { enabled: boolean }) => api.patch(`/api/rules/${id}/toggle`, body),
    test: (id: string, body: unknown) => api.post(`/api/rules/${id}/test`, body),
  },
  documents: {
    metadata: (id: string | number) => api.get(`/api/documents/${id}/metadata`),
    preview: (id: string | number) => `/api/documents/${id}/metadata`,
    download: (id: string | number) => `/api/documents/${id}/download`,
    thumbnailUrl: (id: string | number) => `/api/statements/documents/${id}/thumb`,
    previewUrl: (id: string | number) => `/api/statements/documents/${id}/preview`,
    downloadUrl: (id: string | number) => `/api/documents/${id}/download`,
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
    orphans: {
      findMatch: (id: string) => api.post(`/api/triage/orphans/${id}/find-match`),
      defer: (id: string) => api.post(`/api/triage/orphans/${id}/defer`),
      selfPay: (id: string) => api.post(`/api/triage/orphans/${id}/self-pay`),
      alreadyPaid: (id: string) => api.post(`/api/triage/orphans/${id}/already-paid`),
      notMedical: (id: string) => api.post(`/api/triage/orphans/${id}/not-medical`),
    },
    paperlessSync: () => api.post('/api/triage/paperless-sync'),
  },
  duplicates: {
    list: (params?: string) => api.get(`/api/duplicates${params ? `?${params}` : ''}`),
    get: (id: string) => api.get(`/api/duplicates/${id}`),
    resolve: (id: string, body: { resolution: string; primary_doc_id?: number }) =>
      api.post(`/api/duplicates/${id}/resolve`, body),
    scan: () => api.post('/api/duplicates/scan'),
    settings: () => api.get<{ auto_detect_enabled: boolean }>('/api/duplicates/settings'),
    updateSettings: (body: { auto_detect_enabled: boolean }) =>
      api.put<{ auto_detect_enabled: boolean }>('/api/duplicates/settings', body),
    checkSingle: (body: { document_id: number }) =>
      api.post('/api/duplicates/check-single', body),
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
  dashboard: {
    get: () => api.get('/api/triage/dashboard'),
    corrections: (params?: string) => api.get(`/api/triage/corrections${params ? `?${params}` : ''}`),
    undoCorrection: (eventId: string) => api.post(`/api/triage/corrections/${eventId}/undo`),
    notificationConfig: () => api.get('/api/triage/notifications/config'),
    updateNotificationConfig: (body: { channel: string; enabled: boolean; config?: unknown }) =>
      api.put('/api/triage/notifications/config', body),
  },
  extraction: {
    patterns: () => api.get('/api/extraction/account-numbers/patterns'),
    extractText: (body: { text: string }) => api.post('/api/extraction/account-numbers/extract-text', body),
    extractDocument: (body: { document_id: number; write_to_paperless?: boolean }) =>
      api.post('/api/extraction/account-numbers/extract', body),
    backfill: (body: { document_ids?: number[]; write_to_paperless?: boolean; limit?: number }) =>
      api.post('/api/extraction/account-numbers/backfill', body),
  },
};
