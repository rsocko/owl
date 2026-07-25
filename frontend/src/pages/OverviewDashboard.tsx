import { useCallback, useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Badge,
  Button,
  Card,
  EmptyState,
  ErrorState,
  LoadingState,
  PageHeader,
  StatCard,
  StatGrid,
  StatusDot,
  Toast,
} from '../components/ui';
import { endpoints } from '../lib/api';
import '../styles/overview-dashboard.css';

type Tone = 'ok' | 'warn' | 'err' | 'info' | 'muted' | 'success' | 'warning' | 'danger';

interface HealthResponse {
  status?: string;
  service?: string;
}

interface ModuleStatusResponse {
  status?: string;
  message?: string;
  documents?: number;
  source_mode?: string;
  read_only?: boolean;
  last_run_available?: boolean;
  model_warning?: string;
}

interface ApiStatusResponse {
  status?: string;
  service?: string;
  modules?: {
    paperless?: ModuleStatusResponse;
    statements?: ModuleStatusResponse;
    eob_matching?: ModuleStatusResponse;
    action_queue?: ModuleStatusResponse;
  };
}

interface StatsModuleItem {
  name?: string;
  status?: string;
  last_sync?: string | null;
  item_count?: number;
  detail?: string;
}

interface StatsResponse {
  actions?: {
    pending?: number;
    critical?: number;
    completed_this_period?: number;
  };
  statements?: {
    tracked?: number;
    missing?: number;
  };
  eob?: {
    matched?: number;
    unmatched?: number;
    unresolved_amount?: number;
  };
  modules?: StatsModuleItem[];
}

interface PaperlessHealthResponse {
  status?: string;
  documents?: number;
  message?: string;
}

interface QueueStatusResponse {
  status?: string;
  database?: {
    pending?: number;
    completed?: number;
    dismissed?: number;
    total?: number;
  };
  progress?: {
    current?: number;
    total?: number;
    phase?: string;
  };
  finished_at?: string | null;
  started_at?: string | null;
}

interface EobRunSummary {
  finished_at?: string | null;
  matches_found?: number;
  high_confidence?: number;
  medium_confidence?: number;
  low_confidence?: number;
}

interface EobResultsResponse {
  status?: string;
  run?: EobRunSummary;
}

interface AlertItem {
  id: number;
  severity?: string;
  module?: string;
  title?: string;
  description?: string;
  created_at?: string | null;
  acknowledged_at?: string | null;
  resolved_at?: string | null;
}

interface AlertsResponse {
  alerts?: AlertItem[];
  total?: number;
}

interface DashboardData {
  health: HealthResponse;
  status: ApiStatusResponse;
  stats: StatsResponse;
  paperless: PaperlessHealthResponse;
  alerts: AlertsResponse;
  queue: QueueStatusResponse;
  eobResults: EobResultsResponse;
}

const numberFormatter = new Intl.NumberFormat('en-US');
const currencyFormatter = new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 0 });

function getErrorMessage(error: unknown): string {
  return error instanceof Error ? error.message : 'Something went wrong while loading the dashboard.';
}

function formatDateTime(value?: string | null): string {
  if (!value) return 'Not available';
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return parsed.toLocaleString();
}

function toModuleTone(status?: string): 'ok' | 'warn' | 'err' {
  switch (status) {
    case 'ok':
    case 'healthy':
      return 'ok';
    case 'degraded':
    case 'warning':
    case 'warn':
    case 'idle':
      return 'warn';
    default:
      return 'err';
  }
}

function toBadgeTone(status?: string): Tone {
  switch (status) {
    case 'ok':
    case 'healthy':
      return 'ok';
    case 'degraded':
    case 'warning':
    case 'warn':
    case 'idle':
      return 'warn';
    case 'info':
      return 'info';
    case 'pending':
      return 'muted';
    default:
      return 'err';
  }
}

function severityTone(severity?: string): Tone {
  switch (severity) {
    case 'critical':
    case 'high':
      return 'danger';
    case 'medium':
      return 'warning';
    case 'low':
      return 'ok';
    default:
      return 'info';
  }
}

export default function OverviewDashboard() {
  const navigate = useNavigate();
  const [dashboard, setDashboard] = useState<DashboardData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [moduleRun, setModuleRun] = useState<'discovery' | 'recommendations' | null>(null);
  const [alertBusyId, setAlertBusyId] = useState<string | null>(null);
  const [toast, setToast] = useState<{ message: string; tone: 'success' | 'error' } | null>(null);

  const loadDashboard = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      const [health, status, stats, paperless, alerts, queue, eobResults] = (await Promise.all([
        endpoints.health(),
        endpoints.status(),
        endpoints.stats(),
        endpoints.paperlessHealth(),
        endpoints.alerts.list('limit=5&resolved=false'),
        endpoints.actionQueue.status(),
        endpoints.eob.results(),
      ])) as [
        HealthResponse,
        ApiStatusResponse,
        StatsResponse,
        PaperlessHealthResponse,
        AlertsResponse,
        QueueStatusResponse,
        EobResultsResponse,
      ];

      setDashboard({ health, status, stats, paperless, alerts, queue, eobResults });
    } catch (err) {
      setError(getErrorMessage(err));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadDashboard();
  }, [loadDashboard]);

  useEffect(() => {
    if (!toast) return undefined;
    const timeoutId = window.setTimeout(() => setToast(null), 4000);
    return () => window.clearTimeout(timeoutId);
  }, [toast]);

  const statementsModule = dashboard?.status.modules?.statements;
  const paperlessModule = dashboard?.status.modules?.paperless;
  const eobModule = dashboard?.status.modules?.eob_matching;
  const queueModule = dashboard?.status.modules?.action_queue;
  const alerts = dashboard?.alerts.alerts ?? [];

  const statsModules = useMemo(() => {
    const items = dashboard?.stats.modules ?? [];
    return {
      statements: items.find((item) => item.name === 'statements'),
      eob: items.find((item) => item.name === 'eob-matching'),
      queue: items.find((item) => item.name === 'action-queue'),
    };
  }, [dashboard?.stats.modules]);

  const runStatementsAction = useCallback(
    async (action: 'discovery' | 'recommendations') => {
      try {
        setModuleRun(action);
        if (action === 'discovery') {
          await endpoints.statements.discoveryRun();
          setToast({ message: 'Statement discovery run completed.', tone: 'success' });
        } else {
          const todayIso = new Date().toISOString().slice(0, 10);
          await endpoints.statements.recommendationsRun(todayIso);
          setToast({ message: 'Statement recommendations run completed.', tone: 'success' });
        }
        await loadDashboard();
      } catch (err) {
        setToast({ message: getErrorMessage(err), tone: 'error' });
      } finally {
        setModuleRun(null);
      }
    },
    [loadDashboard],
  );

  const updateAlert = useCallback(async (alertId: number, action: 'acknowledge' | 'resolve') => {
    try {
      setAlertBusyId(`${action}-${alertId}`);
      const updated = (action === 'acknowledge'
        ? await endpoints.alerts.acknowledge(String(alertId))
        : await endpoints.alerts.resolve(String(alertId))) as AlertItem;

      setDashboard((current) => {
        if (!current) return current;
        const nextAlerts = action === 'resolve'
          ? (current.alerts.alerts ?? []).filter((alert) => alert.id !== alertId)
          : (current.alerts.alerts ?? []).map((alert) => (alert.id === alertId ? { ...alert, ...updated } : alert));

        return {
          ...current,
          alerts: {
            ...current.alerts,
            alerts: nextAlerts,
            total: action === 'resolve' ? Math.max((current.alerts.total ?? nextAlerts.length) - 1, 0) : current.alerts.total,
          },
        };
      });
      setToast({ message: action === 'resolve' ? 'Alert resolved.' : 'Alert acknowledged.', tone: 'success' });
    } catch (err) {
      setToast({ message: getErrorMessage(err), tone: 'error' });
    } finally {
      setAlertBusyId(null);
    }
  }, []);

  return (
    <>
      <PageHeader
        title="Overview"
        desc="Unified status across Paperless connectivity, statements, EOB matching, and the action queue."
        actions={<Button onClick={() => void loadDashboard()} disabled={loading}>Refresh</Button>}
      />

      {loading && !dashboard ? <LoadingState label="Loading dashboard…" /> : null}
      {!loading && error && !dashboard ? <ErrorState message={error} onRetry={() => void loadDashboard()} /> : null}

      {dashboard ? (
        <>
          <StatGrid>
            <StatCard
              title="Paperless connectivity"
              metric={dashboard.paperless.documents !== undefined ? numberFormatter.format(dashboard.paperless.documents) : '—'}
              desc={
                <span className="overview-status-inline">
                  <StatusDot tone={toModuleTone(paperlessModule?.status ?? dashboard.paperless.status)} />
                  {dashboard.paperless.status === 'ok' ? 'Documents visible in Paperless' : dashboard.paperless.message ?? 'Connectivity issue detected'}
                </span>
              }
              status={{ label: paperlessModule?.status ?? dashboard.paperless.status ?? 'unknown', tone: toBadgeTone(paperlessModule?.status ?? dashboard.paperless.status) }}
            />
            <StatCard
              title="Statements module"
              metric={numberFormatter.format(dashboard.stats.statements?.missing ?? 0)}
              desc={`${numberFormatter.format(dashboard.stats.statements?.tracked ?? 0)} tracked providers`}
              status={{ label: statementsModule?.status ?? 'unknown', tone: toBadgeTone(statementsModule?.status) }}
            />
            <StatCard
              title="EOB module"
              metric={numberFormatter.format(dashboard.stats.eob?.unmatched ?? 0)}
              desc={`${numberFormatter.format(dashboard.stats.eob?.matched ?? 0)} confirmed matches · ${currencyFormatter.format(dashboard.stats.eob?.unresolved_amount ?? 0)}`}
              status={{ label: eobModule?.status ?? 'unknown', tone: toBadgeTone(eobModule?.status) }}
            />
            <StatCard
              title="Action queue"
              metric={numberFormatter.format(dashboard.queue.database?.pending ?? dashboard.stats.actions?.pending ?? 0)}
              desc={`${numberFormatter.format(dashboard.stats.actions?.completed_this_period ?? 0)} completed this period`}
              status={{ label: queueModule?.status ?? dashboard.queue.status ?? 'unknown', tone: toBadgeTone(queueModule?.status ?? dashboard.queue.status) }}
            />
          </StatGrid>

          <div className="overview-panels section">
            <Card
              title={
                <div className="overview-card-title">
                  <StatusDot tone={toModuleTone(statementsModule?.status)} />
                  <span>Statements</span>
                </div>
              }
            >
              <div className="overview-panel-metrics">
                <div>
                  <div className="overview-metric-label">Missing statements</div>
                  <div className="overview-metric-value">{numberFormatter.format(dashboard.stats.statements?.missing ?? 0)}</div>
                </div>
                <div>
                  <div className="overview-metric-label">Providers tracked</div>
                  <div className="overview-metric-value">{numberFormatter.format(dashboard.stats.statements?.tracked ?? 0)}</div>
                </div>
                <div>
                  <div className="overview-metric-label">Last sync</div>
                  <div className="overview-metric-value overview-metric-value-sm">{formatDateTime(statsModules.statements?.last_sync)}</div>
                </div>
              </div>
              <div className="btn-group">
                <Button variant="primary" onClick={() => void runStatementsAction('discovery')} disabled={moduleRun !== null}>
                  {moduleRun === 'discovery' ? 'Running discovery…' : 'Run discovery'}
                </Button>
                <Button variant="success" onClick={() => void runStatementsAction('recommendations')} disabled={moduleRun !== null}>
                  {moduleRun === 'recommendations' ? 'Running recommendations…' : 'Run recommendations'}
                </Button>
                <Button onClick={() => navigate('/statements')}>Open statements</Button>
              </div>
            </Card>

            <Card
              title={
                <div className="overview-card-title">
                  <StatusDot tone={toModuleTone(eobModule?.status)} />
                  <span>EOB matching</span>
                </div>
              }
            >
              <div className="overview-panel-metrics">
                <div>
                  <div className="overview-metric-label">Candidate matches</div>
                  <div className="overview-metric-value">{numberFormatter.format(dashboard.stats.eob?.unmatched ?? 0)}</div>
                </div>
                <div>
                  <div className="overview-metric-label">Confirmed matches</div>
                  <div className="overview-metric-value">{numberFormatter.format(dashboard.stats.eob?.matched ?? 0)}</div>
                </div>
                <div>
                  <div className="overview-metric-label">Last run</div>
                  <div className="overview-metric-value overview-metric-value-sm">{formatDateTime(dashboard.eobResults.run?.finished_at)}</div>
                </div>
              </div>
              <div className="overview-inline-summary">
                <Badge tone="info">High {numberFormatter.format(dashboard.eobResults.run?.high_confidence ?? 0)}</Badge>
                <Badge tone="warning">Medium {numberFormatter.format(dashboard.eobResults.run?.medium_confidence ?? 0)}</Badge>
                <Badge tone="danger">Low {numberFormatter.format(dashboard.eobResults.run?.low_confidence ?? 0)}</Badge>
              </div>
              <div className="btn-group">
                <Button onClick={() => navigate('/eob')}>Open EOB module</Button>
              </div>
            </Card>

            <Card
              title={
                <div className="overview-card-title">
                  <StatusDot tone={toModuleTone(queueModule?.status ?? dashboard.queue.status)} />
                  <span>Action queue</span>
                </div>
              }
            >
              <div className="overview-panel-metrics">
                <div>
                  <div className="overview-metric-label">Pending</div>
                  <div className="overview-metric-value">{numberFormatter.format(dashboard.queue.database?.pending ?? 0)}</div>
                </div>
                <div>
                  <div className="overview-metric-label">Completed</div>
                  <div className="overview-metric-value">{numberFormatter.format(dashboard.queue.database?.completed ?? 0)}</div>
                </div>
                <div>
                  <div className="overview-metric-label">Last run</div>
                  <div className="overview-metric-value overview-metric-value-sm">{formatDateTime(dashboard.queue.finished_at ?? dashboard.queue.started_at ?? statsModules.queue?.last_sync)}</div>
                </div>
              </div>
              <div className="overview-inline-summary">
                <Badge tone="warning">Critical {numberFormatter.format(dashboard.stats.actions?.critical ?? 0)}</Badge>
                <Badge tone="muted">Total {numberFormatter.format(dashboard.queue.database?.total ?? 0)}</Badge>
              </div>
              <div className="btn-group">
                <Button onClick={() => navigate('/action-queue')}>Open action queue</Button>
              </div>
            </Card>

            <Card title="Recent alerts" actions={<Badge tone={alerts.length > 0 ? 'warning' : 'ok'}>{numberFormatter.format(alerts.length)} unresolved</Badge>}>
              {alerts.length === 0 ? (
                <EmptyState title="No unresolved alerts" desc="Recent alert activity will appear here as modules emit new issues." />
              ) : (
                <div className="alert-list">
                  {alerts.map((alert) => {
                    const acknowledgeBusy = alertBusyId === `acknowledge-${alert.id}`;
                    const resolveBusy = alertBusyId === `resolve-${alert.id}`;
                    return (
                      <div key={alert.id} className="alert-item">
                        <div className="alert-header-row">
                          <div>
                            <div className="alert-title-row">
                              <Badge tone={severityTone(alert.severity)}>{alert.severity ?? 'info'}</Badge>
                              <Badge tone="muted">{alert.module ?? 'module'}</Badge>
                              {alert.acknowledged_at ? <Badge tone="info">Acknowledged</Badge> : null}
                            </div>
                            <div className="alert-title">{alert.title ?? 'Untitled alert'}</div>
                          </div>
                          <div className="alert-date">{formatDateTime(alert.created_at)}</div>
                        </div>
                        <div className="alert-description">{alert.description ?? 'No description provided.'}</div>
                        <div className="alert-actions">
                          <Button size="sm" onClick={() => void updateAlert(alert.id, 'acknowledge')} disabled={Boolean(alert.acknowledged_at) || acknowledgeBusy || resolveBusy}>
                            {acknowledgeBusy ? 'Saving…' : 'Acknowledge'}
                          </Button>
                          <Button size="sm" variant="success" onClick={() => void updateAlert(alert.id, 'resolve')} disabled={acknowledgeBusy || resolveBusy}>
                            {resolveBusy ? 'Resolving…' : 'Resolve'}
                          </Button>
                        </div>
                      </div>
                    );
                  })}
                </div>
              )}
            </Card>
          </div>
        </>
      ) : null}

      {toast ? <Toast message={toast.message} tone={toast.tone} /> : null}
    </>
  );
}
