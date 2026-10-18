import { useEffect, useState } from 'react';
import { Badge, Button, Card, ErrorState, PageHeader, Toast } from '../components/ui';
import { endpoints } from '../lib/api';
import { getToastDuration } from '../lib/toast';
import { buildBackfillBody, buildQueueRunBody } from './actionQueueRunBody';
import '../styles/relationship-maintenance.css';

type Health = {
  status?: string;
  read_only?: boolean;
  paperless?: { status?: string };
  ollama?: { status?: string; model?: string };
};

type ToastState = { message: string; tone?: 'success' | 'error' } | null;
type RescanScope = 'configured' | 'all' | 'document' | 'tags' | 'saved_view';

type ProjectionHealth = {
  pending: number;
  failed: number;
  not_intended: number;
  last_error: {
    relationship_id: string;
    code: string;
    message: string;
    recorded_at: string | null;
  } | null;
};

type ProjectionRepairResult = {
  attempted: number;
  succeeded: number;
  failed: number;
  remaining: number;
  errors: Array<{ relationship_id: string; code: string; message: string }>;
};

type MetadataSchemaReadiness = {
  status: 'ready' | 'missing' | 'incompatible' | 'write_disabled'
    | 'not_configured' | 'permission_denied' | 'endpoint_unavailable';
  category: string | null;
  write_enabled: boolean;
  can_setup: boolean;
  ready_count: number;
  total_count: number;
  fields: Array<{
    key: string;
    name: string;
    type: string;
    options: string[];
    status: 'ready' | 'missing' | 'incompatible';
    diagnostics: string[];
  }>;
};

export default function ActionQueueOperations() {
  const [health, setHealth] = useState<Health | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [toast, setToast] = useState<ToastState>(null);
  const [projectionHealth, setProjectionHealth] = useState<ProjectionHealth | null>(null);
  const [projectionHealthError, setProjectionHealthError] = useState<string | null>(null);
  const [projectionRepair, setProjectionRepair] = useState<ProjectionRepairResult | null>(null);
  const [rescanScope, setRescanScope] = useState<RescanScope>('configured');
  const [metadataReadiness, setMetadataReadiness] = useState<MetadataSchemaReadiness | null>(
    null,
  );
  const [filters, setFilters] = useState({
    document_id: '',
    tag_override: '',
    saved_view_id: '',
    correspondent: '',
    document_type: '',
    created_after: '',
    created_before: '',
    added_after: '',
    added_before: '',
    limit: '',
  });

  useEffect(() => {
    if (!toast) return undefined;
    const timeout = window.setTimeout(
      () => setToast(null),
      getToastDuration(toast.tone),
    );
    return () => window.clearTimeout(timeout);
  }, [toast]);

  const loadProjectionHealth = async () => {
    setProjectionHealthError(null);
    try {
      setProjectionHealth(
        await endpoints.relationships.projectionHealth() as ProjectionHealth,
      );
    } catch (err) {
      setProjectionHealthError(
        err instanceof Error ? err.message : 'Projection health could not be loaded.',
      );
    }
  };

  const loadMetadataReadiness = async () => {
    try {
      setMetadataReadiness(
        await endpoints.actionQueue.checkMetadataSchema() as MetadataSchemaReadiness,
      );
    } catch {
      setMetadataReadiness({
        status: 'endpoint_unavailable',
        category: 'paperless_custom_fields_unavailable',
        write_enabled: false,
        can_setup: false,
        ready_count: 0,
        total_count: 0,
        fields: [],
      });
    }
  };

  useEffect(() => {
    void loadProjectionHealth();
    void loadMetadataReadiness();
  }, []);

  const execute = async (key: string, operation: () => Promise<unknown>, success: string) => {
    setBusy(key);
    setError(null);
    try {
      await operation();
      setToast({ message: success, tone: 'success' });
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Operation failed.';
      setError(message);
      setToast({ message, tone: 'error' });
    } finally {
      setBusy(null);
    }
  };

  const buildScopedRunBody = (dryRun: boolean, force: boolean) => {
    const documentId = rescanScope === 'document' ? filters.document_id : '';
    const body: Record<string, unknown> = buildQueueRunBody(dryRun, documentId);
    if (force) body.force = true;
    if (rescanScope === 'all') body.all_documents = true;
    if (rescanScope === 'document' && documentId) body.document_id = Number(documentId);
    if (rescanScope === 'tags' && filters.tag_override) {
      body.tag_override = filters.tag_override;
    }
    if (rescanScope === 'saved_view' && filters.saved_view_id) {
      body.saved_view_id = Number(filters.saved_view_id);
    }
    if (rescanScope === 'all' || rescanScope === 'tags') {
      if (filters.correspondent) body.correspondent = filters.correspondent;
      if (filters.document_type) body.document_type = filters.document_type;
      if (filters.created_after) body.created_after = filters.created_after;
      if (filters.created_before) body.created_before = filters.created_before;
      if (filters.added_after) body.added_after = filters.added_after;
      if (filters.added_before) body.added_before = filters.added_before;
    }
    if (rescanScope !== 'document' && filters.limit) body.limit = Number(filters.limit);
    return body;
  };

  const runScoped = (dryRun: boolean, force: boolean) => {
    if (
      force
      && !dryRun
      && !window.confirm(
        'Force reanalysis for the selected scope? Existing actions may be updated or resolved.',
      )
    ) {
      return;
    }
    const body = buildScopedRunBody(dryRun, force);
    return execute(
      dryRun ? 'rescan-preview' : force ? 'force-rescan' : 'scoped-run',
      () => endpoints.actionQueue.run(body),
      dryRun
        ? 'Force-rescan preview completed.'
        : force
          ? 'Forced rescan completed.'
          : 'Scoped run completed.',
    );
  };

  const repairRelationshipProjections = async () => {
    setBusy('relationship-projections');
    setError(null);
    setProjectionRepair(null);
    try {
      const result = (
        await endpoints.relationships.reconcileProjections()
      ) as ProjectionRepairResult;
      setProjectionRepair(result);
      await loadProjectionHealth();
      setToast({
        message: result.failed
          ? `Repair completed with ${result.failed} failed projection${result.failed === 1 ? '' : 's'}.`
          : 'Paperless relationship sync repaired.',
        tone: result.failed ? 'error' : 'success',
      });
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Relationship sync repair failed.';
      setError(message);
      setToast({ message, tone: 'error' });
    } finally {
      setBusy(null);
    }
  };

  const scopeReady = (
    (rescanScope !== 'document' || Boolean(filters.document_id))
    && (rescanScope !== 'tags' || Boolean(filters.tag_override.trim()))
    && (rescanScope !== 'saved_view' || Boolean(filters.saved_view_id))
  );

  return (
    <>
      <PageHeader
        title="Action Queue Operations"
        desc="Pipeline health, diagnostics, dry runs, backfills, and one-off troubleshooting."
        actions={<a href="#/action-queue">Return to Action Queue</a>}
      />

      {error && <ErrorState message={error} />}

      <div className="settings-grid">
        <Card title="Health and diagnostics">
          <div className="aq-meta-list">
            <div className="aq-meta-row">
              <span>Pipeline</span>
              <Badge tone={health?.status === 'ok' ? 'success' : 'muted'}>
                {health?.status ?? 'Not checked'}
              </Badge>
            </div>
            <div className="aq-meta-row">
              <span>Paperless</span>
              <span>{health?.paperless?.status ?? '—'}</span>
            </div>
            <div className="aq-meta-row">
              <span>Analyzer</span>
              <span>{health?.ollama?.status ?? '—'} {health?.ollama?.model ?? ''}</span>
            </div>
          </div>
          <div className="btn-group">
            <Button
              onClick={() => void execute(
                'health',
                async () => setHealth(await endpoints.actionQueue.check() as Health),
                'Service check completed.',
              )}
              disabled={busy !== null}
            >
              Check services
            </Button>
          </div>
        </Card>

        <Card title="Paperless metadata schema">
          <p className="text-muted">
            Validate every active OWL field, including durable document metadata, action status,
            and relationship projections. Setup creates missing fields, renames approved aliases,
            and adds required select choices.
          </p>
          <div className="aq-meta-list" aria-live="polite">
            <div className="aq-meta-row">
              <span>Schema</span>
              <Badge
                tone={
                  metadataReadiness?.status === 'ready'
                    ? 'success'
                    : metadataReadiness?.status === 'incompatible'
                      ? 'danger'
                      : 'warning'
                }
              >
                {metadataReadiness?.status.replaceAll('_', ' ') ?? 'Checking…'}
              </Badge>
            </div>
            <div className="aq-meta-row">
              <span>Ready fields</span>
              <span>
                {metadataReadiness
                  ? `${metadataReadiness.ready_count} of ${metadataReadiness.total_count}`
                  : '—'}
              </span>
            </div>
          </div>
          {metadataReadiness?.fields.length ? (
            <details>
              <summary>Review field status</summary>
              <div className="aq-meta-list">
                {metadataReadiness.fields.map((field) => (
                  <div className="aq-meta-row" key={field.key}>
                    <span>
                      {field.name}
                      <span className="text-muted">
                        {' · '}{field.type}
                        {field.options.length ? ` · ${field.options.join(', ')}` : ''}
                      </span>
                    </span>
                    <Badge
                      tone={
                        field.status === 'ready'
                          ? 'success'
                          : field.status === 'incompatible'
                            ? 'danger'
                            : 'warning'
                      }
                    >
                      {field.status}
                    </Badge>
                  </div>
                ))}
              </div>
            </details>
          ) : null}
          {metadataReadiness?.status === 'incompatible' && (
            <p className="relationship-maintenance__warning" role="alert">
              Resolve duplicate or incorrectly typed fields in Paperless before setup. OWL did not
              change the schema.
            </p>
          )}
          {metadataReadiness?.status === 'write_disabled' && (
            <p className="text-muted">
              Enable Paperless writeback before setting up metadata fields.
            </p>
          )}
          <div className="btn-group">
            <Button
              onClick={() => void execute(
                'metadata-schema-check',
                loadMetadataReadiness,
                'Paperless metadata schema checked.',
              )}
              disabled={busy !== null}
            >
              {busy === 'metadata-schema-check' ? 'Checking schema…' : 'Check metadata schema'}
            </Button>
            {metadataReadiness?.can_setup && (
              <Button
                variant="primary"
                onClick={() => void execute(
                  'metadata-schema-setup',
                  async () => {
                    const readiness = await endpoints.actionQueue.setupMetadataSchema();
                    setMetadataReadiness(readiness as MetadataSchemaReadiness);
                    await loadProjectionHealth();
                  },
                  'Complete Paperless metadata schema is ready.',
                )}
                disabled={busy !== null}
              >
                {busy === 'metadata-schema-setup'
                  ? 'Setting up schema…'
                  : 'Set up complete metadata schema'}
              </Button>
            )}
          </div>
        </Card>

        <Card title="Scoped run and rescan">
          <p className="text-muted">
            Run new or stale documents normally, or force OWL to reanalyze every document in
            the selected scope. Preview shows the forced candidate set without changing actions.
          </p>
          <div className="form-row">
            <div className="form-group">
              <label htmlFor="ops-rescan-scope">Scope</label>
              <select
                id="ops-rescan-scope"
                value={rescanScope}
                onChange={(event) => setRescanScope(event.target.value as RescanScope)}
              >
                <option value="configured">Configured Action Queue source</option>
                <option value="all">All Paperless documents</option>
                <option value="document">Single document</option>
                <option value="tags">Tag or tags</option>
                <option value="saved_view">Saved view</option>
              </select>
            </div>
            {rescanScope === 'document' && (
              <div className="form-group">
                <label htmlFor="ops-document-id">Document ID</label>
                <input
                  id="ops-document-id"
                  type="number"
                  min={1}
                  required
                  value={filters.document_id}
                  onChange={(event) => setFilters((current) => ({
                    ...current,
                    document_id: event.target.value,
                  }))}
                />
              </div>
            )}
            {rescanScope === 'tags' && (
              <div className="form-group">
                <label htmlFor="ops-tags">Tags</label>
                <input
                  id="ops-tags"
                  placeholder="Inbox, Receipt"
                  required
                  value={filters.tag_override}
                  onChange={(event) => setFilters((current) => ({
                    ...current,
                    tag_override: event.target.value,
                  }))}
                />
              </div>
            )}
            {rescanScope === 'saved_view' && (
              <div className="form-group">
                <label htmlFor="ops-saved-view">Saved view ID</label>
                <input
                  id="ops-saved-view"
                  type="number"
                  min={1}
                  required
                  value={filters.saved_view_id}
                  onChange={(event) => setFilters((current) => ({
                    ...current,
                    saved_view_id: event.target.value,
                  }))}
                />
              </div>
            )}
            {(rescanScope === 'all' || rescanScope === 'tags') && (
              <>
                <div className="form-group">
                  <label htmlFor="ops-correspondent">Correspondent</label>
                  <input
                    id="ops-correspondent"
                    value={filters.correspondent}
                    onChange={(event) => setFilters((current) => ({
                      ...current,
                      correspondent: event.target.value,
                    }))}
                  />
                </div>
                <div className="form-group">
                  <label htmlFor="ops-document-type">Document type</label>
                  <input
                    id="ops-document-type"
                    value={filters.document_type}
                    onChange={(event) => setFilters((current) => ({
                      ...current,
                      document_type: event.target.value,
                    }))}
                  />
                </div>
                <div className="form-group">
                  <label htmlFor="ops-created-after">Created after</label>
                  <input
                    id="ops-created-after"
                    type="date"
                    value={filters.created_after}
                    onChange={(event) => setFilters((current) => ({
                      ...current,
                      created_after: event.target.value,
                    }))}
                  />
                </div>
                <div className="form-group">
                  <label htmlFor="ops-created-before">Created before</label>
                  <input
                    id="ops-created-before"
                    type="date"
                    value={filters.created_before}
                    onChange={(event) => setFilters((current) => ({
                      ...current,
                      created_before: event.target.value,
                    }))}
                  />
                </div>
                <div className="form-group">
                  <label htmlFor="ops-added-after">Added after</label>
                  <input
                    id="ops-added-after"
                    type="date"
                    value={filters.added_after}
                    onChange={(event) => setFilters((current) => ({
                      ...current,
                      added_after: event.target.value,
                    }))}
                  />
                </div>
                <div className="form-group">
                  <label htmlFor="ops-added-before">Added before</label>
                  <input
                    id="ops-added-before"
                    type="date"
                    value={filters.added_before}
                    onChange={(event) => setFilters((current) => ({
                      ...current,
                      added_before: event.target.value,
                    }))}
                  />
                </div>
              </>
            )}
            {rescanScope !== 'document' && (
              <div className="form-group">
                <label htmlFor="ops-limit">Limit</label>
                <input
                  id="ops-limit"
                  type="number"
                  min={1}
                  max={500}
                  value={filters.limit}
                  onChange={(event) => setFilters((current) => ({
                    ...current,
                    limit: event.target.value,
                  }))}
                />
              </div>
            )}
          </div>
          {rescanScope === 'all' && (
            <p className="relationship-maintenance__warning" role="alert">
              All-document rescans can be expensive. Use filters, a limit, and Preview scope
              before starting a large run.
            </p>
          )}
          <div className="btn-group">
            <Button
              onClick={() => void runScoped(true, true)}
              disabled={busy !== null || !scopeReady}
            >
              Preview scope
            </Button>
            <Button
              onClick={() => void runScoped(false, false)}
              disabled={busy !== null || !scopeReady}
            >
              Run new or stale
            </Button>
            <Button
              variant="danger"
              onClick={() => void runScoped(false, true)}
              disabled={busy !== null || !scopeReady}
            >
              Force rescan
            </Button>
          </div>
        </Card>

        <Card title="Backfill and repair">
          <p className="text-muted">
            Reapply OWL action data to Paperless, or replace OWL's cached document metadata
            with current Paperless values.
          </p>
          <div className="btn-group">
            <Button
              onClick={() => void execute(
                'backfill-preview',
                () => endpoints.actionQueue.backfill(buildBackfillBody(true)),
                'Backfill preview completed.',
              )}
              disabled={busy !== null}
            >
              Backfill preview
            </Button>
            <Button
              variant="primary"
              onClick={() => void execute(
                'backfill',
                () => endpoints.actionQueue.backfill(buildBackfillBody(false)),
                'Paperless backfill completed.',
              )}
              disabled={busy !== null}
            >
              Backfill Paperless
            </Button>
            <Button
              onClick={() => void execute(
                'metadata',
                () => endpoints.actionQueue.refreshMetadata({ force: true }),
                'Action metadata refreshed from Paperless.',
              )}
              disabled={busy !== null}
            >
              Refresh all metadata
            </Button>
          </div>
          <div className="relationship-maintenance">
            <div className="relationship-maintenance__intro">
              <strong>Paperless relationship sync</strong>
              <span className="text-muted">
                Repair relationship fields that have not reached Paperless. OWL remains
                authoritative.
              </span>
            </div>
            {projectionHealth ? (
              <dl className="relationship-maintenance__metrics">
                <div className="relationship-maintenance__metric">
                  <dt>Pending</dt>
                  <dd>{projectionHealth.pending}</dd>
                </div>
                <div className="relationship-maintenance__metric">
                  <dt>Failed</dt>
                  <dd>{projectionHealth.failed}</dd>
                </div>
              </dl>
            ) : (
              <span className="relationship-maintenance__status">
                {projectionHealthError ?? 'Loading projection health…'}
              </span>
            )}
            {projectionHealth?.last_error && (
              <p className="relationship-maintenance__status">
                Last error ({projectionHealth.last_error.code.replaceAll('_', ' ')}):
                {' '}{projectionHealth.last_error.message}
              </p>
            )}
            <Button
              onClick={() => void repairRelationshipProjections()}
              disabled={
                busy !== null
                || !projectionHealth
                || projectionHealth.pending + projectionHealth.failed === 0
              }
            >
              {busy === 'relationship-projections'
                ? 'Repairing relationship sync…'
                : 'Retry failed Paperless relationship sync'}
            </Button>
            {projectionHealth
              && projectionHealth.pending + projectionHealth.failed === 0
              && (
                <span className="relationship-maintenance__status">
                  No relationship projections need repair.
                </span>
              )}
            {projectionRepair && (
              <div className="relationship-maintenance__panel" aria-live="polite">
                <dl className="relationship-maintenance__metrics">
                  <div className="relationship-maintenance__metric">
                    <dt>Attempted</dt>
                    <dd>{projectionRepair.attempted}</dd>
                  </div>
                  <div className="relationship-maintenance__metric">
                    <dt>Succeeded</dt>
                    <dd>{projectionRepair.succeeded}</dd>
                  </div>
                  <div className="relationship-maintenance__metric">
                    <dt>Remaining</dt>
                    <dd>{projectionRepair.remaining}</dd>
                  </div>
                </dl>
                {projectionRepair.errors.length > 0 && (
                  <ul className="relationship-maintenance__errors">
                    {projectionRepair.errors.map((item) => (
                      <li key={item.relationship_id}>
                        {item.message} ({item.code.replaceAll('_', ' ')})
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            )}
          </div>
        </Card>
      </div>

      {toast && <Toast message={toast.message} tone={toast.tone} onDismiss={() => setToast(null)} />}
    </>
  );
}
