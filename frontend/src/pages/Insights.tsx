import { useEffect, useMemo, useState } from 'react';
import { Badge, Button, Card, DataTable, ErrorState, FilterPills, PageHeader, SkeletonLoader, StatCard, StatGrid, Toast } from '../components/ui';
import { endpoints } from '../lib/api';

type ToastState = {
  message: string;
  tone: 'success' | 'error';
};

type AlertItem = {
  id: number | string;
  alert_type?: string | null;
  severity?: string | null;
  module?: string | null;
  title?: string | null;
  description?: string | null;
  action_url?: string | null;
  metadata?: Record<string, unknown> | null;
  created_at?: string | null;
  acknowledged_at?: string | null;
  resolved_at?: string | null;
};

type AlertsResponse = {
  alerts?: AlertItem[];
  total?: number;
};

type AlertSummary = {
  total?: number;
  unacknowledged?: number;
  by_severity?: Record<string, number>;
  by_module?: Record<string, number>;
};

const severityOptions = [
  { key: 'all', label: 'All severities' },
  { key: 'critical', label: 'Critical' },
  { key: 'high', label: 'High' },
  { key: 'medium', label: 'Medium' },
  { key: 'low', label: 'Low' },
  { key: 'info', label: 'Info' },
];

const statusOptions = [
  { key: 'open', label: 'Open' },
  { key: 'unacknowledged', label: 'Unacknowledged' },
  { key: 'acknowledged', label: 'Acknowledged' },
  { key: 'resolved', label: 'Resolved' },
];

const moduleOptions = [
  { key: 'all', label: 'All modules' },
  { key: 'statements', label: 'Statements' },
  { key: 'eob', label: 'EOB matching' },
  { key: 'action_queue', label: 'Action queue' },
];

function getErrorMessage(error: unknown) {
  return error instanceof Error ? error.message : 'Something went wrong.';
}

function formatDateTime(value?: string | null) {
  if (!value) return '—';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}

function toneForSeverity(severity?: string | null): 'danger' | 'warning' | 'muted' | 'info' {
  switch ((severity ?? '').toLowerCase()) {
    case 'critical':
    case 'high':
      return 'danger';
    case 'medium':
      return 'warning';
    case 'info':
      return 'info';
    default:
      return 'muted';
  }
}

function toneForStatus(alert: AlertItem): 'success' | 'info' | 'warning' {
  if (alert.resolved_at) return 'success';
  if (alert.acknowledged_at) return 'info';
  return 'warning';
}

function statusLabel(alert: AlertItem) {
  if (alert.resolved_at) return 'Resolved';
  if (alert.acknowledged_at) return 'Acknowledged';
  return 'Open';
}

export default function Insights() {
  const [summary, setSummary] = useState<AlertSummary | null>(null);
  const [alerts, setAlerts] = useState<AlertItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [cleaningUp, setCleaningUp] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [toast, setToast] = useState<ToastState | null>(null);
  const [severityFilter, setSeverityFilter] = useState('all');
  const [statusFilter, setStatusFilter] = useState('open');
  const [moduleFilter, setModuleFilter] = useState('all');

  useEffect(() => {
    if (!toast) return undefined;
    const timeout = window.setTimeout(() => setToast(null), 3200);
    return () => window.clearTimeout(timeout);
  }, [toast]);

  const buildParams = () => {
    const params = new URLSearchParams({ limit: '100' });
    if (severityFilter !== 'all') params.set('severity', severityFilter);
    if (moduleFilter !== 'all') params.set('module', moduleFilter);
    if (statusFilter === 'resolved') {
      params.set('resolved', 'true');
    } else {
      params.set('resolved', 'false');
      if (statusFilter === 'acknowledged') params.set('acknowledged', 'true');
      if (statusFilter === 'unacknowledged') params.set('acknowledged', 'false');
    }
    return params.toString();
  };

  const loadData = async () => {
    setLoading(true);
    setError(null);
    try {
      const [summaryResponse, listResponse] = await Promise.all([
        endpoints.alerts.summary() as Promise<AlertSummary>,
        endpoints.alerts.list(buildParams()) as Promise<AlertsResponse>,
      ]);
      setSummary(summaryResponse);
      setAlerts(Array.isArray(listResponse.alerts) ? listResponse.alerts : []);
    } catch (err) {
      setError(getErrorMessage(err));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void loadData();
  }, [severityFilter, statusFilter, moduleFilter]);

  const criticalAndHigh = useMemo(() => {
    return (summary?.by_severity?.critical ?? 0) + (summary?.by_severity?.high ?? 0);
  }, [summary]);

  const topModule = useMemo(() => {
    const modules = Object.entries(summary?.by_module ?? {});
    if (modules.length === 0) return '—';
    const [name, count] = modules.sort((a, b) => b[1] - a[1])[0];
    return `${name.replace('_', ' ')} (${count})`;
  }, [summary]);

  const handleAction = async (kind: 'acknowledge' | 'resolve', alertId: number | string) => {
    const key = `${kind}:${alertId}`;
    setBusyId(key);
    try {
      if (kind === 'acknowledge') {
        await endpoints.alerts.acknowledge(String(alertId));
      } else {
        await endpoints.alerts.resolve(String(alertId));
      }
      await loadData();
      setToast({ message: kind === 'acknowledge' ? 'Alert acknowledged.' : 'Alert resolved.', tone: 'success' });
    } catch (err) {
      setToast({ message: getErrorMessage(err), tone: 'error' });
    } finally {
      setBusyId(null);
    }
  };

  const handleCleanup = async () => {
    setCleaningUp(true);
    try {
      await endpoints.alerts.cleanup();
      await loadData();
      setToast({ message: 'Retention cleanup completed.', tone: 'success' });
    } catch (err) {
      setToast({ message: getErrorMessage(err), tone: 'error' });
    } finally {
      setCleaningUp(false);
    }
  };

  return (
    <>
      <PageHeader
        title="Insights"
        desc="Track alert volume, severity, and module health across statements, EOB matching, and the action queue."
        actions={
          <div className="btn-group">
            <Button onClick={() => void loadData()} disabled={loading || cleaningUp}>
              Refresh
            </Button>
            <Button variant="primary" onClick={() => void handleCleanup()} disabled={loading || cleaningUp}>
              {cleaningUp ? 'Cleaning…' : 'Run cleanup'}
            </Button>
          </div>
        }
      />

      {toast && <Toast message={toast.message} tone={toast.tone} />}

      {loading ? (
        <><SkeletonLoader variant="stat-grid" /><div className="section"><SkeletonLoader variant="table" /></div></>
      ) : error ? (
        <ErrorState message={error} onRetry={() => void loadData()} />
      ) : (
        <>
          <StatGrid>
            <StatCard title="Open alerts" metric={summary?.total ?? 0} desc="Currently unresolved items in the unified feed." />
            <StatCard title="Needs acknowledgement" metric={summary?.unacknowledged ?? 0} desc="Still unseen or untriaged by an operator." status={{ label: (summary?.unacknowledged ?? 0) > 0 ? 'Attention' : 'Clear', tone: (summary?.unacknowledged ?? 0) > 0 ? 'warning' : 'success' }} />
            <StatCard title="Critical + high" metric={criticalAndHigh} desc="The highest-severity issues currently active." status={{ label: criticalAndHigh > 0 ? 'Escalated' : 'Stable', tone: criticalAndHigh > 0 ? 'danger' : 'success' }} />
            <StatCard title="Busiest module" metric={topModule} desc="Module with the most unresolved alerts right now." />
          </StatGrid>

          <div className="section" style={{ marginTop: 20 }}>
            <Card title="Filter the alert feed">
              <div style={{ display: 'grid', gap: 12 }}>
                <div>
                  <div className="section-title" style={{ marginBottom: 8 }}>Severity</div>
                  <FilterPills options={severityOptions} active={severityFilter} onChange={setSeverityFilter} />
                </div>
                <div>
                  <div className="section-title" style={{ marginBottom: 8 }}>Status</div>
                  <FilterPills options={statusOptions} active={statusFilter} onChange={setStatusFilter} />
                </div>
                <div>
                  <div className="section-title" style={{ marginBottom: 8 }}>Module</div>
                  <FilterPills options={moduleOptions} active={moduleFilter} onChange={setModuleFilter} />
                </div>
              </div>
            </Card>
          </div>

          <div className="section">
            <Card title="Alert feed" actions={<Badge tone="info">{alerts.length} rows</Badge>}>
              <DataTable
                rows={alerts}
                rowKey={(row) => String(row.id)}
                emptyLabel="No alerts match the current filters."
                columns={[
                  {
                    key: 'severity',
                    header: 'Severity',
                    width: '120px',
                    render: (row) => <Badge tone={toneForSeverity(row.severity)}>{row.severity ?? 'unknown'}</Badge>,
                  },
                  {
                    key: 'title',
                    header: 'Alert',
                    render: (row) => (
                      <div>
                        <div style={{ fontWeight: 700 }}>{row.title ?? 'Untitled alert'}</div>
                        <div className="text-muted" style={{ fontSize: '0.82rem', marginTop: 4 }}>{row.description ?? 'No description provided.'}</div>
                        {row.action_url && (
                          <a href={row.action_url} style={{ color: 'var(--accent)', fontSize: '0.78rem', marginTop: 6, display: 'inline-block' }}>
                            Open related view ?
                          </a>
                        )}
                      </div>
                    ),
                  },
                  {
                    key: 'module',
                    header: 'Module',
                    width: '130px',
                    render: (row) => <Badge tone="info">{(row.module ?? 'unknown').replace('_', ' ')}</Badge>,
                  },
                  {
                    key: 'status',
                    header: 'Status',
                    width: '130px',
                    render: (row) => <Badge tone={toneForStatus(row)}>{statusLabel(row)}</Badge>,
                  },
                  {
                    key: 'created',
                    header: 'Created',
                    width: '180px',
                    render: (row) => formatDateTime(row.created_at),
                  },
                  {
                    key: 'actions',
                    header: 'Actions',
                    width: '220px',
                    render: (row) => (
                      <div className="btn-group" style={{ justifyContent: 'flex-end' }}>
                        <Button
                          size="sm"
                          onClick={() => void handleAction('acknowledge', row.id)}
                          disabled={Boolean(row.acknowledged_at || row.resolved_at || busyId !== null)}
                        >
                          {busyId === `acknowledge:${row.id}` ? 'Saving…' : 'Acknowledge'}
                        </Button>
                        <Button
                          size="sm"
                          variant="primary"
                          onClick={() => void handleAction('resolve', row.id)}
                          disabled={Boolean(row.resolved_at || busyId !== null)}
                        >
                          {busyId === `resolve:${row.id}` ? 'Saving…' : 'Resolve'}
                        </Button>
                      </div>
                    ),
                  },
                ]}
              />
            </Card>
          </div>
        </>
      )}
    </>
  );
}

