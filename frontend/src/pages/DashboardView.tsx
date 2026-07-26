import { useCallback, useEffect, useMemo, useState } from 'react';
import { NavLink } from 'react-router-dom';
import {
  Badge,
  Button,
  Card,
  EmptyState,
  ErrorState,
  PageHeader,
  SkeletonLoader,
  StatCard,
  StatGrid,
} from '../components/ui';
import { endpoints } from '../lib/api';
import '../styles/dashboard-view.css';

// ------------------------------------------------------------------
// Types
// ------------------------------------------------------------------

interface DashboardStats {
  pending_count: number;
  match_rate: number;
  triaged_this_month: number;
  extraction_accuracy: number;
}

interface QueueBreakdown {
  type: string;
  count: number;
}

interface MatchRateTrend {
  month: string;
  rate: number;
  confirmed: number;
  total: number;
}

interface ActivityItem {
  id: string;
  event_type: string;
  target_type: string;
  target_id: string;
  payload: Record<string, unknown>;
  paperless_synced: boolean;
  created_at: string | null;
  created_by: string;
}

interface DashboardResponse {
  stats: DashboardStats;
  queue_breakdown: QueueBreakdown[];
  by_status: Record<string, number>;
  match_rate_trend: MatchRateTrend[];
  activity_feed: ActivityItem[];
  queue_stats: Record<string, unknown>;
}

// ------------------------------------------------------------------
// Helpers
// ------------------------------------------------------------------

function formatRelativeTime(isoStr: string | null): string {
  if (!isoStr) return '';
  const diff = Date.now() - new Date(isoStr).getTime();
  const minutes = Math.floor(diff / 60000);
  if (minutes < 1) return 'just now';
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  if (days === 1) return 'yesterday';
  return `${days} days ago`;
}

const EVENT_TYPE_CONFIG: Record<string, { icon: string; label: string; iconClass: string }> = {
  triage_confirm: { icon: '✅', label: 'Confirmed', iconClass: 'match' },
  triage_bulk_confirm_threshold: { icon: '✅', label: 'Auto-confirmed', iconClass: 'match' },
  triage_reject: { icon: '❌', label: 'Rejected', iconClass: 'reject' },
  triage_defer: { icon: '⏸', label: 'Deferred', iconClass: 'orphan' },
  triage_dismissed: { icon: '🗑', label: 'Dismissed', iconClass: 'meta' },
  triage_manual_link: { icon: '🔗', label: 'Manually linked', iconClass: 'merge' },
};

const QUEUE_TYPE_CONFIG: Record<string, { icon: string; color: string }> = {
  eob_match_review: { icon: '📋', color: 'var(--accent)' },
  orphan_document: { icon: '👻', color: 'var(--warning)' },
  grouping_anomaly: { icon: '📂', color: 'var(--teal, #14b8a6)' },
};

function getEventConfig(eventType: string) {
  return EVENT_TYPE_CONFIG[eventType] || { icon: '📝', label: eventType.replace('triage_', ''), iconClass: 'meta' };
}

function getQueueTypeConfig(type: string) {
  return QUEUE_TYPE_CONFIG[type] || { icon: '📄', color: 'var(--muted)' };
}

// ------------------------------------------------------------------
// Component
// ------------------------------------------------------------------

export default function DashboardView() {
  const [data, setData] = useState<DashboardResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const loadDashboard = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const result = await endpoints.dashboard.get() as DashboardResponse;
      setData(result);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load dashboard');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadDashboard();
  }, [loadDashboard]);

  const maxBreakdownCount = useMemo(() => {
    if (!data?.queue_breakdown.length) return 1;
    return Math.max(...data.queue_breakdown.map((q) => q.count), 1);
  }, [data?.queue_breakdown]);

  if (loading && !data) {
    return (
      <>
        <PageHeader title="Dashboard" desc="Triage overview, match rate trends, and recent activity." />
        <SkeletonLoader variant="stat-grid" />
        <div className="section"><SkeletonLoader variant="cards" /></div>
      </>
    );
  }

  if (error && !data) {
    return (
      <>
        <PageHeader title="Dashboard" desc="Triage overview, match rate trends, and recent activity." />
        <ErrorState message={error} onRetry={() => void loadDashboard()} />
      </>
    );
  }

  if (!data) return null;

  const { stats, queue_breakdown, match_rate_trend, activity_feed } = data;

  return (
    <>
      <PageHeader
        title="Dashboard"
        desc="Triage overview, match rate trends, and recent activity."
        actions={
          <Button onClick={() => void loadDashboard()} disabled={loading}>Refresh</Button>
        }
      />

      {/* View switcher (matching mockup tab bar) */}
      <div className="dv-view-tabs">
        <NavLink to="/dashboard-view" className={({ isActive }) => `dv-view-tab${isActive ? ' active' : ''}`}>📊 Dashboard</NavLink>
        <NavLink to="/corrections" className={({ isActive }) => `dv-view-tab${isActive ? ' active' : ''}`}>📜 Correction History</NavLink>
      </div>

      {/* Stats row */}
      <StatGrid>
        <StatCard
          title="Pending Triage"
          metric={stats.pending_count}
          desc="Items awaiting human review"
          status={stats.pending_count > 10 ? { label: 'High', tone: 'danger' } : stats.pending_count > 0 ? { label: 'Active', tone: 'warning' } : { label: 'Clear', tone: 'ok' }}
        />
        <StatCard
          title="Match Rate"
          metric={`${stats.match_rate}%`}
          desc="Auto-match confirmation rate"
          status={{ label: stats.match_rate >= 90 ? 'Excellent' : stats.match_rate >= 75 ? 'Good' : 'Needs attention', tone: stats.match_rate >= 90 ? 'ok' : stats.match_rate >= 75 ? 'warning' : 'danger' }}
        />
        <StatCard
          title="Triaged This Month"
          metric={stats.triaged_this_month}
          desc="Resolved triage items this period"
        />
        <StatCard
          title="Extraction Accuracy"
          metric={`${stats.extraction_accuracy}%`}
          desc="Corrections improving model"
          status={{ label: stats.extraction_accuracy >= 85 ? 'Good' : 'Training', tone: stats.extraction_accuracy >= 85 ? 'ok' : 'warning' }}
        />
      </StatGrid>

      <div className="dv-grid section">
        {/* Left column: Queue breakdown + Match rate chart */}
        <div className="dv-left">
          <Card title="📥 Pending Queue Breakdown">
            {queue_breakdown.length === 0 ? (
              <EmptyState title="No pending items" desc="The triage queue is clear." />
            ) : (
              <div className="dv-queue-breakdown">
                {queue_breakdown.map((item) => {
                  const config = getQueueTypeConfig(item.type);
                  const pct = Math.round((item.count / maxBreakdownCount) * 100);
                  return (
                    <div key={item.type} className="dv-qb-row">
                      <span className="dv-qb-type">{config.icon} {item.type.replace(/_/g, ' ')}</span>
                      <div className="dv-qb-bar-bg">
                        <div className="dv-qb-bar" style={{ width: `${pct}%`, background: config.color }} />
                      </div>
                      <span className="dv-qb-count">{item.count}</span>
                    </div>
                  );
                })}
              </div>
            )}
          </Card>

          <Card title="🎯 Auto-Match Rate (6 months)" className="dv-mt16">
            {match_rate_trend.length === 0 ? (
              <EmptyState title="No trend data" desc="Match rate data will appear as corrections accumulate." />
            ) : (
              <div className="dv-match-chart">
                {match_rate_trend.map((item) => (
                  <div key={item.month} className="dv-mc-row">
                    <span className="dv-mc-label">{item.month}</span>
                    <div className="dv-mc-bar-bg">
                      <div
                        className="dv-mc-bar"
                        style={{
                          width: `${item.rate}%`,
                          background: item.rate >= 90 ? 'var(--success, #2ecc71)' : item.rate >= 80 ? 'var(--accent, #3498db)' : 'var(--warning, #f39c12)',
                        }}
                      >
                        {item.rate > 0 ? `${item.rate}%` : ''}
                      </div>
                    </div>
                  </div>
                ))}
                <div className="dv-mc-note">📈 Match rate improves as correction data trains the extraction model</div>
              </div>
            )}
          </Card>
        </div>

        {/* Right column: Activity feed + Notifications */}
        <div className="dv-right">
          <Card title="🕐 Recent Activity">
            {activity_feed.length === 0 ? (
              <EmptyState title="No recent activity" desc="Correction and triage activity will appear here." />
            ) : (
              <div className="dv-activity-list">
                {activity_feed.map((item) => {
                  const config = getEventConfig(item.event_type);
                  return (
                    <div key={item.id} className="dv-activity-item">
                      <div className={`dv-activity-icon ${config.iconClass}`}>{config.icon}</div>
                      <div className="dv-activity-text">
                        <strong>{config.label}</strong>{' '}
                        {item.target_type === 'eob_match' ? 'EOB match' : item.target_type.replace(/_/g, ' ')}:{' '}
                        {item.target_id}
                        {item.paperless_synced && <Badge tone="ok">synced</Badge>}
                      </div>
                      <span className="dv-activity-time">{formatRelativeTime(item.created_at)}</span>
                    </div>
                  );
                })}
              </div>
            )}
          </Card>

          <Card title="🔔 Notifications & Schedule" className="dv-mt16">
            <div className="dv-notifications">
              <div>📧 <strong>Weekly digest:</strong> Sundays 9 AM — triage summary + pending items</div>
              <div>🚨 <strong>MC alerts:</strong> Real-time for escalated items (orphans &gt;60d, spend spikes &gt;50%)</div>
              <div>📊 <strong>Analysis runs:</strong> Daily 2 AM (basic rules), Weekly Sun 3 AM (LLM rules)</div>
            </div>
          </Card>
        </div>
      </div>
    </>
  );
}
