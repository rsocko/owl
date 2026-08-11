import { useEffect, useMemo, useState } from 'react';
import { api, endpoints } from '../lib/api';
import { Badge, Button, Card, ErrorState, PageHeader, SkeletonLoader, StatCard, StatGrid, Toast } from '../components/ui';
import { getToastDuration } from '../lib/toast';
import { CronScheduleEditor } from '../components/CronScheduleEditor';

type ToastState = {
  message: string;
  tone: 'success' | 'error';
};

type SettingsResponse = {
  llm_base_url?: string | null;
  llm_model?: string | null;
  write_to_paperless?: boolean | null;
  paperless_url?: string | null;
  ollama_url?: string | null;
  ollama_model?: string | null;
};

type ModelsResponse = {
  available?: boolean | null;
  configured_model?: string | null;
  available_models?: string[];
  base_url?: string | null;
  message?: string | null;
};

type LlmTestResponse = {
  status?: string;
  base_url?: string;
  model?: string;
  response?: string;
  message?: string;
};

type PaperlessHealth = {
  status?: string;
  message?: string;
  [key: string]: unknown;
};

type PaperlessStats = {
  status?: string;
  document_count?: number;
  tag_count?: number;
  correspondent_count?: number;
};

type ScheduleConfig = {
  cron?: string;
  limit?: number;
  enabled?: boolean;
};

type SchedulesResponse = {
  statement_discovery?: ScheduleConfig;
  statement_gap_check?: ScheduleConfig;
  action_queue?: ScheduleConfig;
  eob_matching?: ScheduleConfig;
};

type ConnectionDraft = {
  paperlessUrl: string;
  paperlessToken: string;
  writeToPaperless: boolean;
};

type LlmDraft = {
  baseUrl: string;
  model: string;
};

type ScheduleDrafts = {
  statement_discovery: Required<ScheduleConfig>;
  statement_gap_check: Required<ScheduleConfig>;
  action_queue: Required<ScheduleConfig>;
  eob_matching: Required<ScheduleConfig>;
};

type RetentionPolicy = {
  processing_history_days: number;
  alerts_days: number;
  actions_days: number;
  matches_days: number;
  discovery_runs_days: number;
};

type StorageStats = {
  total_size_bytes?: number;
  total_size_human?: string;
  databases?: Array<{ name: string; size_bytes?: number; size_human?: string; exists?: boolean }>;
  tables?: Array<{ database: string; table: string; label?: string; module?: string; row_count?: number }>;
  [key: string]: unknown;
};

type CleanupResult = {
  status?: string;
  dry_run?: boolean;
  [key: string]: unknown;
};

type DocumentTypeEntry = {
  id: number;
  name: string;
  suggested: boolean;
  enabled: boolean;
};

type DocumentTypesResponse = {
  types: DocumentTypeEntry[];
  has_saved_mapping: boolean;
};

function getErrorMessage(error: unknown) {
  return error instanceof Error ? error.message : 'Something went wrong.';
}

function normalizeSchedules(payload?: SchedulesResponse | null): ScheduleDrafts {
  return {
    statement_discovery: {
      cron: payload?.statement_discovery?.cron ?? '0 9 * * *',
      limit: payload?.statement_discovery?.limit ?? 50,
      enabled: payload?.statement_discovery?.enabled ?? true,
    },
    statement_gap_check: {
      cron: payload?.statement_gap_check?.cron ?? '30 9 * * *',
      limit: payload?.statement_gap_check?.limit ?? 50,
      enabled: payload?.statement_gap_check?.enabled ?? true,
    },
    action_queue: {
      cron: payload?.action_queue?.cron ?? '0 */6 * * *',
      limit: payload?.action_queue?.limit ?? 50,
      enabled: payload?.action_queue?.enabled ?? true,
    },
    eob_matching: {
      cron: payload?.eob_matching?.cron ?? '0 2 * * *',
      limit: payload?.eob_matching?.limit ?? 200,
      enabled: payload?.eob_matching?.enabled ?? true,
    },
  };
}

const DEFAULT_RETENTION: RetentionPolicy = {
  processing_history_days: 90,
  alerts_days: 30,
  actions_days: 365,
  matches_days: 365,
  discovery_runs_days: 365,
};

export default function Settings() {
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [toast, setToast] = useState<ToastState | null>(null);
  const [connectionSaving, setConnectionSaving] = useState(false);
  const [llmSaving, setLlmSaving] = useState(false);
  const [llmTesting, setLlmTesting] = useState(false);
  const [scheduleSaving, setScheduleSaving] = useState(false);
  const [retentionSaving, setRetentionSaving] = useState(false);
  const [cleanupRunning, setCleanupRunning] = useState(false);

  // Action Queue source settings
  const [aqSourceLoading, setAqSourceLoading] = useState(false);
  const [aqSourceSaving, setAqSourceSaving] = useState(false);
  const [aqSource, setAqSource] = useState<{
    scan_mode: string;
    monitor_tags: string[];
    saved_view_id: number | null;
    confidence_threshold: number;
    document_limit: number | null;
    rate_limit_delay: number;
    remove_source_tag_on_resolve: boolean;
  }>({
    scan_mode: 'tags',
    monitor_tags: ['Inbox'],
    saved_view_id: null,
    confidence_threshold: 70,
    document_limit: null,
    rate_limit_delay: 0.25,
    remove_source_tag_on_resolve: true,
  });
  const [aqAvailableTags, setAqAvailableTags] = useState<Array<{ id: number; name: string }>>([]);
  const [aqSavedViews, setAqSavedViews] = useState<Array<{ id: number; name: string }>>([]);
  const [aqNewTag, setAqNewTag] = useState('');

  const [connection, setConnection] = useState<ConnectionDraft>({
    paperlessUrl: '',
    paperlessToken: '',
    writeToPaperless: false,
  });
  const [llm, setLlm] = useState<LlmDraft>({ baseUrl: '', model: '' });
  const [schedules, setSchedules] = useState<ScheduleDrafts>(normalizeSchedules());
  const [modelsInfo, setModelsInfo] = useState<ModelsResponse | null>(null);
  const [llmTestResult, setLlmTestResult] = useState<LlmTestResponse | null>(null);
  const [paperlessHealth, setPaperlessHealth] = useState<PaperlessHealth | null>(null);
  const [paperlessStats, setPaperlessStats] = useState<PaperlessStats | null>(null);
  const [retention, setRetention] = useState<RetentionPolicy>(DEFAULT_RETENTION);
  const [storageStats, setStorageStats] = useState<StorageStats | null>(null);
  const [cleanupPreview, setCleanupPreview] = useState<CleanupResult | null>(null);
  const [documentTypes, setDocumentTypes] = useState<DocumentTypeEntry[]>([]);
  const [documentTypesLoading, setDocumentTypesLoading] = useState(false);
  const [documentTypesSaving, setDocumentTypesSaving] = useState(false);
  const [documentTypesHasSaved, setDocumentTypesHasSaved] = useState(false);

  useEffect(() => {
    if (!toast) return undefined;
    const duration = getToastDuration(toast.tone);
    if (duration <= 0) return undefined;
    const timeout = window.setTimeout(() => setToast(null), duration);
    return () => window.clearTimeout(timeout);
  }, [toast]);

  const loadSettings = async () => {
    setLoading(true);
    setError(null);
    try {
      const [settingsResponse, schedulesResponse, modelsResponse, healthResponse, statsResponse, retentionResponse, storageResponse] = await Promise.all([
        endpoints.settings.get() as Promise<SettingsResponse>,
        endpoints.admin.schedules.get() as Promise<SchedulesResponse>,
        endpoints.llm.models() as Promise<ModelsResponse>,
        endpoints.paperlessHealth() as Promise<PaperlessHealth>,
        api.get<PaperlessStats>('/api/paperless/stats'),
        endpoints.admin.retention.get() as Promise<RetentionPolicy>,
        endpoints.admin.storage() as Promise<StorageStats>,
      ]);

      setConnection({
        paperlessUrl: settingsResponse.paperless_url ?? '',
        paperlessToken: '',
        writeToPaperless: Boolean(settingsResponse.write_to_paperless),
      });
      setLlm({
        baseUrl: settingsResponse.llm_base_url ?? settingsResponse.ollama_url ?? '',
        model: settingsResponse.llm_model ?? settingsResponse.ollama_model ?? '',
      });
      setSchedules(normalizeSchedules(schedulesResponse));
      setModelsInfo(modelsResponse);
      setPaperlessHealth(healthResponse);
      setPaperlessStats(statsResponse);
      setRetention({ ...DEFAULT_RETENTION, ...retentionResponse });
      setStorageStats(storageResponse);
      setLlmTestResult(null);
      setCleanupPreview(null);
    } catch (err) {
      setError(getErrorMessage(err));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void loadSettings();
    void loadAqSourceSettings();
  }, []);

  const loadAqSourceSettings = async () => {
    setAqSourceLoading(true);
    try {
      const [settingsResp, tagsResp, viewsResp] = await Promise.all([
        endpoints.actionQueue.settings() as Promise<typeof aqSource>,
        endpoints.actionQueue.metadataTags() as Promise<{ tags: Array<{ id: number; name: string }> }>,
        endpoints.actionQueue.metadataSavedViews() as Promise<{ saved_views: Array<{ id: number; name: string }> }>,
      ]);
      setAqSource({
        scan_mode: settingsResp.scan_mode ?? 'tags',
        monitor_tags: settingsResp.monitor_tags ?? ['Inbox'],
        saved_view_id: settingsResp.saved_view_id ?? null,
        confidence_threshold: settingsResp.confidence_threshold ?? 70,
        document_limit: settingsResp.document_limit ?? null,
        rate_limit_delay: settingsResp.rate_limit_delay ?? 0.25,
        remove_source_tag_on_resolve: settingsResp.remove_source_tag_on_resolve ?? true,
      });
      setAqAvailableTags(tagsResp.tags ?? []);
      setAqSavedViews(viewsResp.saved_views ?? []);
    } catch {
      // Don't block the page if AQ settings fail to load
    } finally {
      setAqSourceLoading(false);
    }
  };

  const handleSaveAqSource = async () => {
    setAqSourceSaving(true);
    try {
      await endpoints.actionQueue.updateSettings(aqSource);
      setToast({ message: 'Action Queue source settings saved.', tone: 'success' });
    } catch (err) {
      setToast({ message: getErrorMessage(err), tone: 'error' });
    } finally {
      setAqSourceSaving(false);
    }
  };

  const handleAddAqTag = () => {
    const tag = aqNewTag.trim();
    if (tag && !aqSource.monitor_tags.includes(tag)) {
      setAqSource((prev) => ({ ...prev, monitor_tags: [...prev.monitor_tags, tag] }));
    }
    setAqNewTag('');
  };

  const handleRemoveAqTag = (tag: string) => {
    setAqSource((prev) => ({
      ...prev,
      monitor_tags: prev.monitor_tags.filter((t) => t !== tag),
    }));
  };

  const modelOptions = useMemo(() => {
    const options = new Set<string>();
    for (const model of modelsInfo?.available_models ?? []) {
      if (model) options.add(model);
    }
    if (llm.model) options.add(llm.model);
    return Array.from(options);
  }, [llm.model, modelsInfo?.available_models]);

  const enabledSchedules = useMemo(
    () => Object.values(schedules).filter((item) => item.enabled).length,
    [schedules],
  );

  const handleSaveConnection = async () => {
    setConnectionSaving(true);
    try {
      await endpoints.settings.update({
        write_to_paperless: connection.writeToPaperless,
      });
      setToast({ message: 'Paperless writeback preference saved.', tone: 'success' });
      await loadSettings();
    } catch (err) {
      setToast({ message: getErrorMessage(err), tone: 'error' });
    } finally {
      setConnectionSaving(false);
    }
  };

  const handleSaveLlm = async () => {
    setLlmSaving(true);
    try {
      await endpoints.settings.update({
        llm_base_url: llm.baseUrl,
        llm_model: llm.model,
      });
      setToast({ message: 'LLM settings saved.', tone: 'success' });
      await loadSettings();
    } catch (err) {
      setToast({ message: getErrorMessage(err), tone: 'error' });
    } finally {
      setLlmSaving(false);
    }
  };

  const handleTestLlm = async () => {
    setLlmTesting(true);
    try {
      const result = await endpoints.llm.test({ base_url: llm.baseUrl, model: llm.model }) as LlmTestResponse;
      setLlmTestResult(result);
      setToast({ message: result.status === 'ok' ? 'LLM connection succeeded.' : (result.message ?? 'LLM connection failed.'), tone: result.status === 'ok' ? 'success' : 'error' });
    } catch (err) {
      setLlmTestResult({ status: 'error', message: getErrorMessage(err) });
      setToast({ message: getErrorMessage(err), tone: 'error' });
    } finally {
      setLlmTesting(false);
    }
  };

  const handleSaveSchedules = async () => {
    setScheduleSaving(true);
    try {
      await endpoints.admin.schedules.update(schedules);
      setToast({ message: 'Schedules saved.', tone: 'success' });
      await loadSettings();
    } catch (err) {
      setToast({ message: getErrorMessage(err), tone: 'error' });
    } finally {
      setScheduleSaving(false);
    }
  };

  const handleSaveRetention = async () => {
    setRetentionSaving(true);
    try {
      await endpoints.admin.retention.update(retention);
      setToast({ message: 'Retention policy saved.', tone: 'success' });
      await loadSettings();
    } catch (err) {
      setToast({ message: getErrorMessage(err), tone: 'error' });
    } finally {
      setRetentionSaving(false);
    }
  };

  const handlePreviewCleanup = async () => {
    setCleanupRunning(true);
    try {
      const result = await endpoints.admin.cleanup({ dry_run: true }) as CleanupResult;
      setCleanupPreview(result);
      setToast({ message: 'Cleanup preview generated (dry run — nothing was deleted).', tone: 'success' });
    } catch (err) {
      setToast({ message: getErrorMessage(err), tone: 'error' });
    } finally {
      setCleanupRunning(false);
    }
  };

  const handleRunCleanup = async () => {
    if (!window.confirm('This will permanently delete stale records per the retention policy above. Continue?')) {
      return;
    }
    setCleanupRunning(true);
    try {
      const result = await endpoints.admin.cleanup({ dry_run: false }) as CleanupResult;
      setCleanupPreview(result);
      setToast({ message: 'Cleanup complete.', tone: 'success' });
      const stats = await endpoints.admin.storage() as StorageStats;
      setStorageStats(stats);
    } catch (err) {
      setToast({ message: getErrorMessage(err), tone: 'error' });
    } finally {
      setCleanupRunning(false);
    }
  };

  const loadDocumentTypes = async () => {
    setDocumentTypesLoading(true);
    try {
      const response = await endpoints.admin.documentTypes() as DocumentTypesResponse;
      setDocumentTypes(response.types);
      setDocumentTypesHasSaved(response.has_saved_mapping);
    } catch (err) {
      setToast({ message: `Document types: ${getErrorMessage(err)}`, tone: 'error' });
    } finally {
      setDocumentTypesLoading(false);
    }
  };

  const handleSaveDocumentTypeMapping = async () => {
    setDocumentTypesSaving(true);
    try {
      await endpoints.admin.documentTypeMapping.update({ types: documentTypes });
      setDocumentTypesHasSaved(true);
      setToast({ message: 'Document type mapping saved.', tone: 'success' });
    } catch (err) {
      setToast({ message: getErrorMessage(err), tone: 'error' });
    } finally {
      setDocumentTypesSaving(false);
    }
  };

  const handleToggleDocumentType = (id: number) => {
    setDocumentTypes((current) =>
      current.map((t) => (t.id === id ? { ...t, enabled: !t.enabled } : t)),
    );
  };

  const enabledDocumentTypeCount = useMemo(
    () => documentTypes.filter((t) => t.enabled).length,
    [documentTypes],
  );

  return (
    <>
      <PageHeader
        title="Settings"
        desc="Manage Paperless connectivity, the LLM gateway, and the scheduled admin jobs exposed by the current backend."
        actions={
          <Button onClick={() => void loadSettings()} disabled={loading || connectionSaving || llmSaving || scheduleSaving || llmTesting}>
            Refresh
          </Button>
        }
      />

      {toast && <Toast message={toast.message} tone={toast.tone} onDismiss={() => setToast(null)} />}

      {loading ? (
        <><SkeletonLoader variant="stat-grid" /><div className="section"><SkeletonLoader variant="cards" /></div></>
      ) : error ? (
        <ErrorState message={error} onRetry={() => void loadSettings()} />
      ) : (
        <>
          <StatGrid>
            <StatCard title="Paperless" metric={paperlessHealth?.status ?? 'unknown'} desc={`${paperlessStats?.document_count ?? 0} docs · ${paperlessStats?.tag_count ?? 0} tags · ${paperlessStats?.correspondent_count ?? 0} correspondents`} status={{ label: paperlessHealth?.status === 'ok' ? 'Connected' : 'Degraded', tone: paperlessHealth?.status === 'ok' ? 'success' : 'warning' }} />
            <StatCard title="Writeback mode" metric={connection.writeToPaperless ? 'Enabled' : 'Read-only'} desc="Controls whether downstream automations can write metadata back to Paperless." status={{ label: connection.writeToPaperless ? 'Writable' : 'Safe mode', tone: connection.writeToPaperless ? 'success' : 'warning' }} />
            <StatCard title="Configured model" metric={llm.model || '—'} desc={llm.baseUrl || 'No LLM gateway URL configured.'} status={{ label: modelsInfo?.available === true ? 'Available' : modelsInfo?.available === false ? 'Missing' : 'Unchecked', tone: modelsInfo?.available === true ? 'success' : modelsInfo?.available === false ? 'danger' : 'warning' }} />
            <StatCard title="Enabled schedules" metric={enabledSchedules} desc="Statement discovery, gap checks, EOB matching, and the action queue are all scheduled by the built-in scheduler." />
          </StatGrid>

          <div className="section" style={{ marginTop: 20 }}>
            <Card title="Paperless connection" actions={<Badge tone={paperlessHealth?.status === 'ok' ? 'success' : 'warning'}>{paperlessHealth?.status ?? 'unknown'}</Badge>}>
              <div className="form-row">
                <div className="form-group">
                  <label htmlFor="paperless-url">Paperless URL</label>
                  <input
                    id="paperless-url"
                    value={connection.paperlessUrl}
                    readOnly
                    aria-readonly="true"
                    style={{ background: 'var(--surface-hover)' }}
                  />
                </div>
                <div className="form-group">
                  <label htmlFor="paperless-token">Paperless API token</label>
                  <input
                    id="paperless-token"
                    type="password"
                    value={connection.paperlessToken}
                    onChange={(event) => setConnection((current) => ({ ...current, paperlessToken: event.target.value }))}
                    placeholder="Not exposed by the runtime settings API"
                    disabled
                    aria-disabled="true"
                    style={{ background: 'var(--surface-hover)' }}
                  />
                </div>
              </div>

              <div
                style={{
                  border: '1px solid var(--border)',
                  borderRadius: 'var(--radius-sm)',
                  padding: 12,
                  marginBottom: 16,
                  background: 'var(--bg)',
                }}
              >
                <label style={{ display: 'flex', alignItems: 'center', gap: 10, fontWeight: 600 }}>
                  <input
                    type="checkbox"
                    checked={connection.writeToPaperless}
                    onChange={(event) => setConnection((current) => ({ ...current, writeToPaperless: event.target.checked }))}
                    style={{ width: 'auto' }}
                  />
                  Write matched metadata back to Paperless
                </label>
                <div className="text-muted" style={{ fontSize: '0.82rem', marginTop: 6 }}>
                  The current settings API exposes the effective URL and writeback toggle, but not token rotation or Paperless URL persistence yet.
                </div>
              </div>

              <div className="btn-group">
                <Button variant="primary" onClick={() => void handleSaveConnection()} disabled={connectionSaving}>
                  {connectionSaving ? 'Saving…' : 'Save connection settings'}
                </Button>
              </div>
            </Card>
          </div>

          <div className="section">
            <Card title="LLM gateway" actions={<Badge tone={modelsInfo?.available === true ? 'success' : modelsInfo?.available === false ? 'danger' : 'warning'}>{modelsInfo?.available === true ? 'Model available' : modelsInfo?.available === false ? 'Model missing' : 'Unverified'}</Badge>}>
              <div className="form-row">
                <div className="form-group">
                  <label htmlFor="llm-base-url">Gateway base URL</label>
                  <input
                    id="llm-base-url"
                    value={llm.baseUrl}
                    onChange={(event) => setLlm((current) => ({ ...current, baseUrl: event.target.value }))}
                    placeholder="https://service-001.example.invalid/openai/v1"
                  />
                </div>
                <div className="form-group">
                  <label htmlFor="llm-model">Model</label>
                  <input
                    id="llm-model"
                    list="llm-model-options"
                    value={llm.model}
                    onChange={(event) => setLlm((current) => ({ ...current, model: event.target.value }))}
                    placeholder="azure/gpt-4o-mini"
                  />
                  <datalist id="llm-model-options">
                    {modelOptions.map((model) => (
                      <option key={model} value={model} />
                    ))}
                  </datalist>
                </div>
              </div>

              {modelsInfo?.message && (
                <div className="text-muted" style={{ fontSize: '0.82rem', marginBottom: 16 }}>
                  {modelsInfo.message}
                </div>
              )}

              {llmTestResult && (
                <div
                  style={{
                    border: '1px solid var(--border)',
                    borderRadius: 'var(--radius-sm)',
                    padding: 12,
                    marginBottom: 16,
                    background: llmTestResult.status === 'ok' ? 'var(--success-bg)' : 'var(--danger-bg)',
                  }}
                >
                  <div style={{ fontWeight: 700 }}>{llmTestResult.status === 'ok' ? 'Connection successful' : 'Connection failed'}</div>
                  <div className="text-muted" style={{ fontSize: '0.82rem', marginTop: 4 }}>
                    {llmTestResult.response ?? llmTestResult.message ?? 'No response body returned.'}
                  </div>
                </div>
              )}

              <div className="btn-group">
                <Button onClick={() => void handleTestLlm()} disabled={llmTesting || llmSaving}>
                  {llmTesting ? 'Testing…' : 'Test connection'}
                </Button>
                <Button variant="primary" onClick={() => void handleSaveLlm()} disabled={llmSaving || llmTesting}>
                  {llmSaving ? 'Saving…' : 'Save LLM settings'}
                </Button>
              </div>
            </Card>
          </div>

          <div className="section">
            <Card title="Schedules" actions={<Badge tone="info">{`${enabledSchedules} of 4 enabled`}</Badge>}>
              <div className="text-muted" style={{ fontSize: '0.82rem', marginBottom: 16 }}>
                Managed by the built-in scheduler. Changes take effect immediately once saved.
              </div>

              <div style={{ display: 'grid', gap: 16 }}>
                {([
                  ['statement_discovery', 'Statement discovery'],
                  ['statement_gap_check', 'Statement gap check'],
                  ['eob_matching', 'EOB matching'],
                  ['action_queue', 'Action queue'],
                ] as const).map(([key, label]) => (
                  <div key={key} style={{ border: '1px solid var(--border)', borderRadius: 'var(--radius-sm)', padding: 16, background: 'var(--bg)' }}>
                    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12, marginBottom: 12 }}>
                      <div style={{ fontWeight: 700 }}>{label}</div>
                      <label style={{ display: 'flex', alignItems: 'center', gap: 8, fontWeight: 600 }}>
                        <input
                          type="checkbox"
                          checked={schedules[key].enabled}
                          onChange={(event) => setSchedules((current) => ({
                            ...current,
                            [key]: {
                              ...current[key],
                              enabled: event.target.checked,
                            },
                          }))}
                          style={{ width: 'auto' }}
                        />
                        Enabled
                      </label>
                    </div>

                    <div className="form-row" style={{ gridTemplateColumns: '1fr' }}>
                      <div className="form-group">
                        <label htmlFor={`${key}-cron`}>Schedule</label>
                        <CronScheduleEditor
                          id={`${key}-cron`}
                          value={schedules[key].cron}
                          onChange={(cron) => setSchedules((current) => ({
                            ...current,
                            [key]: {
                              ...current[key],
                              cron,
                            },
                          }))}
                        />
                      </div>
                      {key !== 'statement_discovery' && key !== 'statement_gap_check' && (
                        <div className="form-group">
                          <label htmlFor={`${key}-limit`}>Document limit</label>
                          <input
                            id={`${key}-limit`}
                            type="number"
                            min={1}
                            max={500}
                            value={schedules[key].limit}
                            onChange={(event) => setSchedules((current) => ({
                              ...current,
                              [key]: {
                                ...current[key],
                                limit: Number(event.target.value) || 1,
                              },
                            }))}
                          />
                        </div>
                      )}
                    </div>
                  </div>
                ))}
              </div>

              <div className="btn-group" style={{ marginTop: 16 }}>
                <Button variant="primary" onClick={() => void handleSaveSchedules()} disabled={scheduleSaving}>
                  {scheduleSaving ? 'Saving…' : 'Save schedules'}
                </Button>
              </div>
            </Card>
          </div>

          <div className="section">
            <Card
              title="Action Queue — Document Source"
              actions={<Badge tone="info">{aqSource.scan_mode === 'tags' ? `Tags: ${aqSource.monitor_tags.join(', ')}` : 'Saved view'}</Badge>}
            >
              <div className="text-muted" style={{ fontSize: '0.82rem', marginBottom: 16 }}>
                Configure what documents the Action Queue pipeline scans by default. Scheduled and manual runs both use this configuration unless overridden with a custom run.
              </div>

              {aqSourceLoading ? (
                <SkeletonLoader variant="cards" />
              ) : (
                <>
                  <div style={{ marginBottom: 16 }}>
                    <div style={{ fontWeight: 700, marginBottom: 8 }}>Default scan mode</div>
                    <div style={{ display: 'flex', gap: 16 }}>
                      <label style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                        <input
                          type="radio"
                          name="aq-scan-mode"
                          value="tags"
                          checked={aqSource.scan_mode === 'tags'}
                          onChange={() => setAqSource((prev) => ({ ...prev, scan_mode: 'tags' }))}
                          style={{ width: 'auto' }}
                        />
                        Tag list
                      </label>
                      <label style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                        <input
                          type="radio"
                          name="aq-scan-mode"
                          value="saved_view"
                          checked={aqSource.scan_mode === 'saved_view'}
                          onChange={() => setAqSource((prev) => ({ ...prev, scan_mode: 'saved_view' }))}
                          style={{ width: 'auto' }}
                        />
                        Saved view
                      </label>
                    </div>
                  </div>

                  {aqSource.scan_mode === 'tags' && (
                    <div style={{ border: '1px solid var(--border)', borderRadius: 'var(--radius-sm)', padding: 12, marginBottom: 16, background: 'var(--bg)' }}>
                      <div style={{ fontWeight: 600, marginBottom: 8 }}>Monitor tags</div>
                      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginBottom: 8 }}>
                        {aqSource.monitor_tags.map((tag) => (
                          <span
                            key={tag}
                            style={{
                              display: 'inline-flex',
                              alignItems: 'center',
                              gap: 4,
                              background: 'var(--surface-hover)',
                              border: '1px solid var(--border)',
                              borderRadius: 'var(--radius-sm)',
                              padding: '2px 8px',
                              fontSize: '0.82rem',
                            }}
                          >
                            {tag}
                            <button
                              onClick={() => handleRemoveAqTag(tag)}
                              disabled={aqSource.monitor_tags.length === 1}
                              style={{ background: 'none', border: 'none', cursor: 'pointer', padding: 0, lineHeight: 1, fontSize: '1rem', color: 'var(--text-muted)' }}
                              aria-label={`Remove tag ${tag}`}
                              title={aqSource.monitor_tags.length === 1 ? 'At least one intake tag is required' : undefined}
                            >
                              ×
                            </button>
                          </span>
                        ))}
                      </div>
                      <div style={{ display: 'flex', gap: 8 }}>
                        <select
                          value={aqNewTag}
                          onChange={(e) => setAqNewTag(e.target.value)}
                          style={{ flex: 1 }}
                        >
                          <option value="">Select a tag…</option>
                          {aqAvailableTags
                            .filter((t) => !aqSource.monitor_tags.includes(t.name))
                            .map((t) => (
                              <option key={t.id} value={t.name}>{t.name}</option>
                            ))
                          }
                        </select>
                        <Button onClick={handleAddAqTag} disabled={!aqNewTag.trim()}>
                          Add
                        </Button>
                      </div>
                    </div>
                  )}

                  {aqSource.scan_mode === 'saved_view' && (
                    <div style={{ border: '1px solid var(--border)', borderRadius: 'var(--radius-sm)', padding: 12, marginBottom: 16, background: 'var(--bg)' }}>
                      <div className="form-group">
                        <label htmlFor="aq-saved-view">Saved view</label>
                        <select
                          id="aq-saved-view"
                          value={aqSource.saved_view_id ?? ''}
                          onChange={(e) => setAqSource((prev) => ({
                            ...prev,
                            saved_view_id: e.target.value ? Number(e.target.value) : null,
                          }))}
                        >
                          <option value="">Select a saved view…</option>
                          {aqSavedViews.map((v) => (
                            <option key={v.id} value={v.id}>{v.name}</option>
                          ))}
                        </select>
                      </div>
                    </div>
                  )}

                  <details style={{ marginBottom: 16 }}>
                    <summary style={{ cursor: 'pointer', fontWeight: 600, marginBottom: 8 }}>Advanced settings</summary>
                    <div className="form-row" style={{ gridTemplateColumns: '1fr 1fr 1fr' }}>
                      <div className="form-group">
                        <label htmlFor="aq-confidence">Confidence threshold (%)</label>
                        <input
                          id="aq-confidence"
                          type="number"
                          min={1}
                          max={100}
                          value={aqSource.confidence_threshold}
                          onChange={(e) => setAqSource((prev) => ({
                            ...prev,
                            confidence_threshold: Number(e.target.value) || 70,
                          }))}
                        />
                      </div>
                      <div className="form-group">
                        <label htmlFor="aq-limit">Document limit per run</label>
                        <input
                          id="aq-limit"
                          type="number"
                          min={1}
                          max={5000}
                          value={aqSource.document_limit ?? ''}
                          placeholder="Unlimited"
                          onChange={(e) => setAqSource((prev) => ({
                            ...prev,
                            document_limit: e.target.value ? Number(e.target.value) : null,
                          }))}
                        />
                      </div>
                      <div className="form-group">
                        <label htmlFor="aq-rate-limit">Rate limit delay (s)</label>
                        <input
                          id="aq-rate-limit"
                          type="number"
                          min={0}
                          max={10}
                          step={0.05}
                          value={aqSource.rate_limit_delay}
                          onChange={(e) => setAqSource((prev) => ({
                            ...prev,
                            rate_limit_delay: Number(e.target.value) || 0,
                          }))}
                        />
                      </div>
                    </div>
                    <label style={{ display: 'flex', alignItems: 'flex-start', gap: 8, marginTop: 12 }}>
                      <input
                        type="checkbox"
                        checked={aqSource.remove_source_tag_on_resolve}
                        onChange={(e) => setAqSource((prev) => ({
                          ...prev,
                          remove_source_tag_on_resolve: e.target.checked,
                        }))}
                        style={{ width: 'auto', marginTop: 3 }}
                      />
                      <span>
                        <strong>Remove intake tags when resolved</strong>
                        <span className="text-muted" style={{ display: 'block', fontSize: '0.78rem' }}>
                          Removes the monitored tags from Paperless when an action is completed,
                          dismissed, or classified as not requiring action.
                        </span>
                      </span>
                    </label>
                  </details>

                  <div className="btn-group">
                    <Button variant="primary" onClick={() => void handleSaveAqSource()} disabled={aqSourceSaving}>
                      {aqSourceSaving ? 'Saving…' : 'Save source settings'}
                    </Button>
                    <Button onClick={() => void loadAqSourceSettings()} disabled={aqSourceLoading}>
                      Refresh
                    </Button>
                  </div>
                </>
              )}
            </Card>
          </div>

          <div className="section">
            <Card
              title="Document type mapping"
              actions={
                documentTypes.length > 0
                  ? <Badge tone={documentTypesHasSaved ? 'success' : 'warning'}>{documentTypesHasSaved ? `${enabledDocumentTypeCount} enabled` : 'Not configured'}</Badge>
                  : undefined
              }
            >
              <div className="text-muted" style={{ fontSize: '0.82rem', marginBottom: 16 }}>
                Configure which Paperless document types are considered "statement-like" for the discovery pipeline.
                Types with a <Badge tone="info">suggested</Badge> badge
                match common statement keywords and are pre-selected when no mapping is saved yet.
              </div>

              {documentTypes.length === 0 ? (
                <div className="btn-group">
                  <Button onClick={() => void loadDocumentTypes()} disabled={documentTypesLoading}>
                    {documentTypesLoading ? 'Fetching…' : 'Fetch document types from Paperless'}
                  </Button>
                </div>
              ) : (
                <>
                  <div
                    style={{
                      border: '1px solid var(--border)',
                      borderRadius: 'var(--radius-sm)',
                      maxHeight: 320,
                      overflowY: 'auto',
                      marginBottom: 16,
                    }}
                  >
                    <table className="data-table" style={{ margin: 0 }}>
                      <thead>
                        <tr>
                          <th style={{ width: 50 }}>Enable</th>
                          <th>Document type</th>
                          <th style={{ width: 90 }}>Status</th>
                        </tr>
                      </thead>
                      <tbody>
                        {documentTypes.map((docType) => (
                          <tr key={docType.id}>
                            <td>
                              <input
                                type="checkbox"
                                checked={docType.enabled}
                                onChange={() => handleToggleDocumentType(docType.id)}
                                style={{ width: 'auto' }}
                              />
                            </td>
                            <td>{docType.name}</td>
                            <td>
                              {docType.suggested && (
                                <Badge tone="info">suggested</Badge>
                              )}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>

                  <div className="btn-group">
                    <Button variant="primary" onClick={() => void handleSaveDocumentTypeMapping()} disabled={documentTypesSaving}>
                      {documentTypesSaving ? 'Saving…' : 'Save mapping'}
                    </Button>
                    <Button onClick={() => void loadDocumentTypes()} disabled={documentTypesLoading}>
                      {documentTypesLoading ? 'Refreshing…' : 'Refresh types'}
                    </Button>
                  </div>
                </>
              )}
            </Card>
          </div>

          <div className="section">
            <Card title="Data retention & storage" actions={<Badge tone="info">{storageStats?.total_size_human ?? (storageStats?.total_size_bytes ? `${(storageStats.total_size_bytes / 1_048_576).toFixed(1)} MB` : 'Storage unknown')}</Badge>}>
              <div className="text-muted" style={{ fontSize: '0.82rem', marginBottom: 16 }}>
                Set any value to 0 to keep records forever. Cleanup permanently deletes stale records past the
                configured age and reclaims disk space — always preview with a dry run first.
              </div>

              <div className="form-row" style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))' }}>
                {([
                  ['processing_history_days', 'Processing history (days)'],
                  ['alerts_days', 'Alerts (days)'],
                  ['actions_days', 'Action queue items (days)'],
                  ['matches_days', 'EOB matches (days)'],
                  ['discovery_runs_days', 'Discovery runs (days)'],
                ] as const).map(([key, label]) => (
                  <div className="form-group" key={key}>
                    <label htmlFor={key}>{label}</label>
                    <input
                      id={key}
                      type="number"
                      min={0}
                      value={retention[key]}
                      onChange={(event) => setRetention((current) => ({
                        ...current,
                        [key]: Number(event.target.value) || 0,
                      }))}
                    />
                  </div>
                ))}
              </div>

              <div className="btn-group" style={{ marginBottom: 16 }}>
                <Button variant="primary" onClick={() => void handleSaveRetention()} disabled={retentionSaving}>
                  {retentionSaving ? 'Saving…' : 'Save retention policy'}
                </Button>
                <Button onClick={() => void handlePreviewCleanup()} disabled={cleanupRunning}>
                  {cleanupRunning ? 'Working…' : 'Preview cleanup (dry run)'}
                </Button>
                <Button variant="danger" onClick={() => void handleRunCleanup()} disabled={cleanupRunning}>
                  {cleanupRunning ? 'Working…' : 'Run cleanup now'}
                </Button>
              </div>

              {cleanupPreview && (
                <div
                  style={{
                    border: '1px solid var(--border)',
                    borderRadius: 'var(--radius-sm)',
                    padding: 12,
                    marginBottom: 16,
                    background: cleanupPreview.dry_run === false ? 'var(--success-bg)' : 'var(--bg)',
                  }}
                >
                  <div style={{ fontWeight: 700 }}>
                    {cleanupPreview.dry_run === false ? 'Cleanup ran' : 'Cleanup preview (nothing deleted)'}
                  </div>
                  <pre style={{ fontSize: '0.78rem', color: 'var(--text-muted)', whiteSpace: 'pre-wrap', marginTop: 6 }}>
                    {JSON.stringify(cleanupPreview, null, 2)}
                  </pre>
                </div>
              )}

              {storageStats?.databases && storageStats.databases.length > 0 && (
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>Database</th>
                      <th>Size</th>
                      <th>Tables</th>
                    </tr>
                  </thead>
                  <tbody>
                    {storageStats.databases.map((db) => (
                      <tr key={db.name}>
                        <td>{db.name}{db.exists === false ? ' (not created yet)' : ''}</td>
                        <td>{db.size_human ?? (db.size_bytes ? `${(db.size_bytes / 1_048_576).toFixed(2)} MB` : '—')}</td>
                        <td>
                          {(storageStats.tables ?? [])
                            .filter((t) => t.database === db.name)
                            .map((t) => `${t.label ?? t.table} (${t.row_count ?? 0})`)
                            .join(', ') || '—'}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </Card>
          </div>
        </>
      )}
    </>
  );
}
