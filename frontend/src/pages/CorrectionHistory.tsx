import { useCallback, useEffect, useState } from 'react';
import { NavLink } from 'react-router-dom';
import {
  Button,
  Card,
  EmptyState,
  ErrorState,
  FilterPills,
  PageHeader,
  SkeletonLoader,
  Toast,
} from '../components/ui';
import { endpoints } from '../lib/api';
import '../styles/dashboard-view.css';  // shared tab bar styles
import '../styles/correction-history.css';

// ------------------------------------------------------------------
// Types
// ------------------------------------------------------------------

interface CorrectionEvent {
  id: string;
  event_type: string;
  target_type: string;
  target_id: string;
  payload: Record<string, unknown>;
  paperless_synced: boolean;
  paperless_synced_at: string | null;
  undone: boolean;
  undone_at: string | null;
  created_at: string | null;
  created_by: string;
}

interface CorrectionsResponse {
  items: CorrectionEvent[];
  count: number;
  offset: number;
  limit: number;
}

// ------------------------------------------------------------------
// Helpers
// ------------------------------------------------------------------

function formatDateTime(isoStr: string | null): string {
  if (!isoStr) return '';
  const d = new Date(isoStr);
  if (Number.isNaN(d.getTime())) return isoStr;
  const now = new Date();
  const diffMs = now.getTime() - d.getTime();
  const hours = Math.floor(diffMs / 3600000);
  if (hours < 1) return `${Math.max(1, Math.floor(diffMs / 60000))}m ago`;
  if (hours < 24) return `Today, ${d.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })}`;
  if (hours < 48) return `Yesterday, ${d.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })}`;
  return d.toLocaleDateString([], { month: 'short', day: 'numeric' });
}

const EVENT_TYPE_MAP: Record<string, { icon: string; label: string; iconClass: string }> = {
  triage_confirm: { icon: '✅', label: 'Confirmed EOB ↔ Bill Match', iconClass: 'match' },
  triage_bulk_confirm_threshold: { icon: '✅', label: 'Bulk Auto-Confirm', iconClass: 'match' },
  triage_reject: { icon: '❌', label: 'Rejected EOB ↔ Bill Match', iconClass: 'reject' },
  triage_defer: { icon: '⏸', label: 'Deferred Item', iconClass: 'orphan' },
  triage_dismissed: { icon: '🗑', label: 'Dismissed', iconClass: 'meta' },
  triage_manual_link: { icon: '🔗', label: 'Manually Linked', iconClass: 'merge' },
};

function getEventDisplay(eventType: string) {
  return EVENT_TYPE_MAP[eventType] || { icon: '📝', label: eventType.replace('triage_', '').replace(/_/g, ' '), iconClass: 'meta' };
}

const FILTER_OPTIONS = [
  { key: 'all', label: 'All' },
  { key: 'match', label: 'Match changes' },
  { key: 'metadata', label: 'Metadata fixes' },
  { key: 'merge', label: 'Merges' },
  { key: 'orphan', label: 'Orphan resolutions' },
  { key: 'series', label: 'Series changes' },
];

function filterToEventTypes(filter: string): string | undefined {
  switch (filter) {
    case 'match': return 'triage_confirm';  // includes confirm + bulk_confirm
    case 'metadata': return 'triage_metadata_correction';
    case 'merge': return 'triage_manual_link';
    case 'orphan': return 'triage_defer';
    case 'series': return 'triage_series_split';
    default: return undefined;  // 'all' — no filter
  }
}

// ------------------------------------------------------------------
// Diff display component
// ------------------------------------------------------------------

function DiffView({ payload }: { payload: Record<string, unknown> }) {
  const changes = (payload.changes || payload.diff || payload) as Record<string, unknown>;

  // Look for before/after or old/new patterns in payload
  const diffEntries: Array<{ label: string; oldVal: string; newVal: string }> = [];

  if (typeof changes === 'object' && changes !== null) {
    for (const [key, value] of Object.entries(changes)) {
      if (key === 'min_confidence' || key === 'score_pct') continue;
      if (Array.isArray(value) && value.length === 2) {
        diffEntries.push({ label: key, oldVal: String(value[0] ?? ''), newVal: String(value[1] ?? '') });
      } else if (typeof value === 'object' && value !== null) {
        const v = value as Record<string, unknown>;
        if ('before' in v && 'after' in v) {
          diffEntries.push({ label: key, oldVal: String(v.before ?? ''), newVal: String(v.after ?? '') });
        } else if ('old' in v && 'new' in v) {
          diffEntries.push({ label: key, oldVal: String(v.old ?? ''), newVal: String(v.new ?? '') });
        }
      }
    }
  }

  if (diffEntries.length === 0) return null;

  return (
    <div className="ch-diff">
      {diffEntries.map((d) => (
        <div key={d.label} className="ch-diff-row">
          <span className="ch-diff-label">{d.label}:</span>
          <span className="ch-diff-old">{d.oldVal || '(empty)'}</span>
          <span className="ch-diff-arrow">→</span>
          <span className="ch-diff-new">{d.newVal || '(empty)'}</span>
        </div>
      ))}
    </div>
  );
}

// ------------------------------------------------------------------
// Main Component
// ------------------------------------------------------------------

export default function CorrectionHistory() {
  const [items, setItems] = useState<CorrectionEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState('all');
  const [toast, setToast] = useState<{ message: string; tone: 'success' | 'error' } | null>(null);
  const [undoingId, setUndoingId] = useState<string | null>(null);

  const loadCorrections = useCallback(async (activeFilter: string) => {
    setLoading(true);
    setError(null);
    try {
      const eventType = filterToEventTypes(activeFilter);
      const params = new URLSearchParams();
      if (eventType) params.set('event_type', eventType);
      params.set('limit', '50');
      const result = await endpoints.dashboard.corrections(params.toString()) as CorrectionsResponse;
      setItems(result.items);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load corrections');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadCorrections(filter);
  }, [filter, loadCorrections]);

  useEffect(() => {
    if (!toast) return undefined;
    const id = window.setTimeout(() => setToast(null), 4000);
    return () => window.clearTimeout(id);
  }, [toast]);

  const handleUndo = useCallback(async (eventId: string) => {
    if (!window.confirm('Undo this correction? The triage item will be reverted to pending.')) return;
    setUndoingId(eventId);
    try {
      await endpoints.dashboard.undoCorrection(eventId);
      setItems((prev) => prev.filter((item) => item.id !== eventId));
      setToast({ message: 'Correction undone successfully.', tone: 'success' });
    } catch (err) {
      setToast({ message: err instanceof Error ? err.message : 'Undo failed', tone: 'error' });
    } finally {
      setUndoingId(null);
    }
  }, []);

  return (
    <>
      <PageHeader
        title="Correction History"
        desc="Chronological record of all triage corrections, with before/after diffs and Paperless sync status."
        actions={
          <Button onClick={() => void loadCorrections(filter)} disabled={loading}>Refresh</Button>
        }
      />

      {/* View switcher (matching mockup tab bar) */}
      <div className="dv-view-tabs">
        <NavLink to="/dashboard-view" className={({ isActive }) => `dv-view-tab${isActive ? ' active' : ''}`}>📊 Dashboard</NavLink>
        <NavLink to="/corrections" className={({ isActive }) => `dv-view-tab${isActive ? ' active' : ''}`}>📜 Correction History</NavLink>
      </div>

      <div className="ch-integration-note">
        <strong>📋 Dual History Sources:</strong> Corrections are tracked in both the DI{' '}
        <code>correction_events</code> table (with training context) and the{' '}
        <strong>Paperless audit log</strong> (django-auditlog, on by default since v2.7). The
        "✅ Synced to Paperless" badges below indicate successful writeback.
      </div>

      <div className="section">
        <Card>
          <FilterPills
            active={filter}
            onChange={setFilter}
            options={FILTER_OPTIONS}
          />
        </Card>
      </div>

      {loading && items.length === 0 ? (
        <div className="section"><SkeletonLoader variant="cards" /></div>
      ) : error ? (
        <ErrorState message={error} onRetry={() => void loadCorrections(filter)} />
      ) : items.length === 0 ? (
        <EmptyState title="No correction events" desc="Corrections will appear here as triage items are resolved." />
      ) : (
        <div className="ch-list section">
          {items.map((item) => {
            const display = getEventDisplay(item.event_type);
            return (
              <div key={item.id} className="ch-entry">
                <div className="ch-header">
                  <div className={`ch-icon ${display.iconClass}`}>{display.icon}</div>
                  <div className="ch-title">{display.label}</div>
                  <div className="ch-time">{formatDateTime(item.created_at)}</div>
                </div>
                <div className="ch-body">
                  <div className="ch-detail">
                    {item.target_type === 'eob_match' ? 'EOB match' : item.target_type.replace(/_/g, ' ')}:{' '}
                    <strong>{item.target_id}</strong>
                  </div>

                  <DiffView payload={item.payload} />

                  <div className="ch-sync-row">
                    {item.paperless_synced ? (
                      <span className="ch-sync-badge synced">✅ Synced to Paperless</span>
                    ) : (
                      <span className="ch-sync-badge pending">⏳ Pending sync</span>
                    )}
                    {item.paperless_synced_at && (
                      <span className="ch-sync-time">{formatDateTime(item.paperless_synced_at)}</span>
                    )}
                  </div>

                  <div className="ch-actions">
                    <button
                      className="ch-undo"
                      onClick={() => void handleUndo(item.id)}
                      disabled={undoingId === item.id}
                    >
                      {undoingId === item.id ? 'Undoing…' : '↩ Undo'}
                    </button>
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      )}

      {toast ? <Toast message={toast.message} tone={toast.tone} /> : null}
    </>
  );
}
