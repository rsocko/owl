import { useCallback, useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import {
  Badge,
  Button,
  Card,
  DataTable,
  EmptyState,
  ErrorState,
  FilterPills,
  LoadingState,
  PageHeader,
  SkeletonLoader,
  StatCard,
  StatGrid,
} from '../components/ui';
import { endpoints } from '../lib/api';
import '../styles/eob-pages.css';

interface UnmatchedEobItem {
  id: string;
  provider?: string | null;
  amount?: number | null;
  date_of_service?: string | null;
  patient_responsibility?: number | null;
  document_url?: string | null;
  created_at?: string | null;
}

type FilterKey = 'all' | 'recent' | 'aging' | 'high-value';

function formatDate(value?: string | null) {
  if (!value) return '—';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleDateString([], {
    month: 'short',
    day: 'numeric',
    year: 'numeric',
  });
}

function formatDateTime(value?: string | null) {
  if (!value) return '—';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString([], {
    month: 'short',
    day: 'numeric',
    year: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  });
}

function formatCurrency(value?: number | null) {
  if (typeof value !== 'number' || Number.isNaN(value)) return '—';
  return new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' }).format(value);
}

function ageInDays(item: UnmatchedEobItem) {
  const source = item.created_at || item.date_of_service;
  if (!source) return null;
  const date = new Date(source);
  if (Number.isNaN(date.getTime())) return null;
  return Math.max(0, Math.floor((Date.now() - date.getTime()) / (1000 * 60 * 60 * 24)));
}

function ageTone(age: number | null): 'success' | 'warning' | 'danger' | 'muted' {
  if (age === null) return 'muted';
  if (age > 30) return 'danger';
  if (age > 14) return 'warning';
  return 'success';
}

export default function EobUnmatched() {
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [items, setItems] = useState<UnmatchedEobItem[]>([]);
  const [activeFilter, setActiveFilter] = useState<FilterKey>('all');

  const loadItems = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const response = (await endpoints.eob.unmatched()) as UnmatchedEobItem[];
      setItems(Array.isArray(response) ? response : []);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load unmatched EOBs.');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadItems();
  }, [loadItems]);

  const filteredItems = useMemo(() => {
    return items.filter((item) => {
      const age = ageInDays(item);
      const amount = item.patient_responsibility ?? item.amount ?? 0;
      switch (activeFilter) {
        case 'recent':
          return age !== null && age <= 7;
        case 'aging':
          return age !== null && age > 14;
        case 'high-value':
          return amount >= 100;
        default:
          return true;
      }
    });
  }, [activeFilter, items]);

  const oldestAge = useMemo(() => {
    const ages = items.map(ageInDays).filter((age): age is number => age !== null);
    return ages.length ? Math.max(...ages) : null;
  }, [items]);

  const totalPatientResponsibility = useMemo(
    () => items.reduce((sum, item) => sum + (item.patient_responsibility ?? 0), 0),
    [items],
  );

  if (loading) {
    return (
      <>
        <PageHeader title="Unmatched EOBs" desc="EOB documents that do not yet have a confirmed bill match." />
        <SkeletonLoader variant="table" rows={6} />
      </>
    );
  }

  if (error) {
    return (
      <>
        <PageHeader title="Unmatched EOBs" desc="EOB documents that do not yet have a confirmed bill match." />
        <ErrorState message={error} onRetry={() => void loadItems()} />
      </>
    );
  }

  return (
    <>
      <PageHeader
        title="Unmatched EOBs"
        desc={
          <div className="eob-meta-row">
            <Link to="/eob" className="eob-link">
              ← Back to dashboard
            </Link>
            <span>These documents have no confirmed claim/bill link yet.</span>
          </div>
        }
        actions={
          <div className="btn-group">
            <Button onClick={() => void loadItems()}>Refresh</Button>
          </div>
        }
      />

      <div className="eob-page-stack">
        <StatGrid>
          <StatCard
            title="Open unmatched EOBs"
            metric={items.length}
            desc="Current EOB queue without a confirmed bill pair"
            status={{ label: items.length ? 'Needs action' : 'Clear', tone: items.length ? 'warning' : 'success' }}
          />
          <StatCard
            title="Oldest age"
            metric={oldestAge !== null ? `${oldestAge}d` : '—'}
            desc="Days since created/service date"
            status={{ label: oldestAge !== null && oldestAge > 30 ? 'Escalate' : 'Normal', tone: oldestAge !== null && oldestAge > 30 ? 'danger' : 'muted' }}
          />
          <StatCard
            title="Patient responsibility"
            metric={formatCurrency(totalPatientResponsibility)}
            desc="Total responsibility represented by the unmatched queue"
          />
        </StatGrid>

        <Card title="Queue filters">
          <div className="eob-card-stack">
            <FilterPills
              active={activeFilter}
              onChange={(value) => setActiveFilter(value as FilterKey)}
              options={[
                { key: 'all', label: `All (${items.length})` },
                { key: 'recent', label: 'Recent ≤ 7d' },
                { key: 'aging', label: 'Aging > 14d' },
                { key: 'high-value', label: 'High value ≥ $100' },
              ]}
            />
            <div className="eob-field-note">
              Manual search opens the triage search page with provider and document context prefilled in the URL.
            </div>
          </div>
        </Card>

        <Card title="Documents waiting for manual match">
          {filteredItems.length ? (
            <DataTable<UnmatchedEobItem>
              rowKey={(item) => item.id}
              rows={filteredItems}
              columns={[
                {
                  key: 'document',
                  header: 'Document',
                  render: (item) => (
                    <div className="eob-table-primary">
                      <strong>{item.provider || 'Unknown provider'}</strong>
                      <span className="eob-table-secondary">EOB #{item.id}</span>
                    </div>
                  ),
                },
                {
                  key: 'service-date',
                  header: 'Date of service',
                  render: (item) => formatDate(item.date_of_service),
                },
                {
                  key: 'amount',
                  header: 'Total billed',
                  render: (item) => formatCurrency(item.amount),
                },
                {
                  key: 'patient-resp',
                  header: 'Patient resp.',
                  render: (item) => formatCurrency(item.patient_responsibility),
                },
                {
                  key: 'age',
                  header: 'Age',
                  render: (item) => {
                    const age = ageInDays(item);
                    return <Badge tone={ageTone(age)}>{age !== null ? `${age} days` : 'Unknown'}</Badge>;
                  },
                },
                {
                  key: 'queued',
                  header: 'Queued',
                  render: (item) => formatDateTime(item.created_at),
                },
                {
                  key: 'actions',
                  header: '',
                  width: '220px',
                  render: (item) => {
                    const params = new URLSearchParams();
                    params.set('docId', item.id);
                    if (item.provider) params.set('provider', item.provider);
                    if (typeof item.patient_responsibility === 'number') {
                      params.set('patientResponsibility', String(item.patient_responsibility));
                    }
                    if (item.date_of_service) params.set('dateOfService', item.date_of_service);
                    return (
                      <div className="eob-actions-end">
                        <Link className="eob-link" to={`/triage/manual-search?${params.toString()}`}>
                          Search manually →
                        </Link>
                        {item.document_url ? (
                          <a className="eob-link-secondary" href={item.document_url} target="_blank" rel="noreferrer">
                            Open source
                          </a>
                        ) : null}
                      </div>
                    );
                  },
                },
              ]}
            />
          ) : (
            <EmptyState
              title="No unmatched EOBs for this filter"
              desc="Try a different filter or refresh after the next pipeline run."
            />
          )}
        </Card>

        <Card title="Follow-up guidance">
          <div className="eob-tip-box">
            <span className="eob-tip-icon">💡</span>
            <div>
              <strong>Tip:</strong> Older items usually mean either the bill has not been uploaded yet or the match signal is too weak
              for auto-linking. Start with aging or high-value EOBs first, then use manual search to inspect likely bill candidates.
            </div>
          </div>
        </Card>
      </div>
    </>
  );
}
