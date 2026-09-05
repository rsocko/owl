import { useEffect, useState } from 'react';
import { Badge, Button, Card, ErrorState, PageHeader, Toast } from '../components/ui';
import { endpoints } from '../lib/api';
import { getToastDuration } from '../lib/toast';
import { buildBackfillBody, buildQueueRunBody } from './actionQueueRunBody';

type Health = {
  status?: string;
  read_only?: boolean;
  paperless?: { status?: string };
  ollama?: { status?: string; model?: string };
};

type ToastState = { message: string; tone?: 'success' | 'error' } | null;

export default function ActionQueueOperations() {
  const [health, setHealth] = useState<Health | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [toast, setToast] = useState<ToastState>(null);
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

  const runCustom = (dryRun: boolean) => {
    const body: Record<string, unknown> = buildQueueRunBody(
      dryRun,
      filters.document_id,
    );
    if (filters.document_id) body.document_id = Number(filters.document_id);
    if (filters.tag_override) body.tag_override = filters.tag_override;
    if (filters.saved_view_id) body.saved_view_id = Number(filters.saved_view_id);
    if (filters.correspondent) body.correspondent = filters.correspondent;
    if (filters.document_type) body.document_type = filters.document_type;
    if (filters.created_after) body.created_after = filters.created_after;
    if (filters.created_before) body.created_before = filters.created_before;
    if (filters.added_after) body.added_after = filters.added_after;
    if (filters.added_before) body.added_before = filters.added_before;
    if (filters.limit) body.limit = Number(filters.limit);
    return execute(
      dryRun ? 'custom-dry' : 'custom-run',
      () => endpoints.actionQueue.run(body),
      dryRun ? 'Dry run completed.' : 'Custom run completed.',
    );
  };

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
            <Button
              onClick={() => void execute(
                'fields',
                () => endpoints.actionQueue.checkCustomFields(),
                'Custom-field check completed.',
              )}
              disabled={busy !== null}
            >
              Check custom fields
            </Button>
          </div>
        </Card>

        <Card title="Custom run">
          <div className="form-row">
            <div className="form-group">
              <label htmlFor="ops-document-id">Document ID</label>
              <input
                id="ops-document-id"
                type="number"
                value={filters.document_id}
                onChange={(event) => setFilters((current) => ({
                  ...current,
                  document_id: event.target.value,
                }))}
              />
            </div>
            <div className="form-group">
              <label htmlFor="ops-tags">Tag override</label>
              <input
                id="ops-tags"
                value={filters.tag_override}
                onChange={(event) => setFilters((current) => ({
                  ...current,
                  tag_override: event.target.value,
                }))}
              />
            </div>
            <div className="form-group">
              <label htmlFor="ops-saved-view">Saved view ID</label>
              <input
                id="ops-saved-view"
                type="number"
                value={filters.saved_view_id}
                onChange={(event) => setFilters((current) => ({
                  ...current,
                  saved_view_id: event.target.value,
                }))}
              />
            </div>
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
          </div>
          <div className="btn-group">
            <Button onClick={() => void runCustom(true)} disabled={busy !== null}>Dry run</Button>
            <Button variant="primary" onClick={() => void runCustom(false)} disabled={busy !== null}>
              Run custom
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
        </Card>
      </div>

      {toast && <Toast message={toast.message} tone={toast.tone} onDismiss={() => setToast(null)} />}
    </>
  );
}
