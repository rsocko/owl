import { useEffect, useMemo, useState } from 'react';
import { Badge, Button, Card, ErrorState, LoadingState, PageHeader, StatCard, StatGrid, Toast } from '../components/ui';
import { endpoints } from '../lib/api';

type WeightsMap = Record<string, number>;

type ToastState = {
  message: string;
  tone: 'success' | 'error';
};

const DEFAULT_WEIGHTS: WeightsMap = {
  date: 0.3,
  provider: 0.25,
  patient: 0.2,
  amount: 0.15,
  procedures: 0.1,
};

const WEIGHT_DESCRIPTIONS: Record<string, string> = {
  date: 'How strongly service and posting dates influence the final EOB-to-bill match score.',
  provider: 'How much provider name alignment should outweigh other fuzzy signals.',
  patient: 'How much patient identity and subscriber matching should contribute.',
  amount: 'How much billed and responsibility amounts should affect the score.',
  procedures: 'How much CPT/procedure overlap and claim detail similarity should matter.',
};

function getErrorMessage(error: unknown) {
  return error instanceof Error ? error.message : 'Something went wrong.';
}

function normalizeWeights(payload: unknown): WeightsMap {
  if (payload && typeof payload === 'object') {
    const maybeWrapped = 'weights' in payload ? (payload as { weights?: unknown }).weights : payload;
    if (maybeWrapped && typeof maybeWrapped === 'object') {
      const entries = Object.entries(maybeWrapped as Record<string, unknown>).filter(([, value]) => typeof value === 'number');
      if (entries.length > 0) {
        return Object.fromEntries(entries) as WeightsMap;
      }
    }
  }
  return { ...DEFAULT_WEIGHTS };
}

function formatLabel(key: string) {
  return key
    .replace(/_/g, ' ')
    .replace(/\b\w/g, (char) => char.toUpperCase());
}

export default function RulesConfig() {
  const [weights, setWeights] = useState<WeightsMap>({});
  const [draftWeights, setDraftWeights] = useState<WeightsMap>({});
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [toast, setToast] = useState<ToastState | null>(null);

  const loadWeights = async () => {
    setLoading(true);
    setError(null);
    try {
      const response = await endpoints.admin.weights.get();
      const normalized = normalizeWeights(response);
      setWeights(normalized);
      setDraftWeights(normalized);
    } catch (err) {
      setError(getErrorMessage(err));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void loadWeights();
  }, []);

  useEffect(() => {
    if (!toast) return undefined;
    const timeout = window.setTimeout(() => setToast(null), 3200);
    return () => window.clearTimeout(timeout);
  }, [toast]);

  const totalWeight = useMemo(
    () => Object.values(draftWeights).reduce((sum, value) => sum + value, 0),
    [draftWeights],
  );

  const deltaCount = useMemo(() => {
    return Object.entries(draftWeights).filter(([key, value]) => weights[key] !== value).length;
  }, [draftWeights, weights]);

  const topSignal = useMemo(() => {
    const first = Object.entries(draftWeights).sort((a, b) => b[1] - a[1])[0];
    return first ? `${formatLabel(first[0])} (${first[1].toFixed(2)})` : '—';
  }, [draftWeights]);

  const canSave = Object.keys(draftWeights).length > 0 && Math.abs(totalWeight - 1) <= 0.05;

  const handleSliderChange = (key: string, value: number) => {
    setDraftWeights((current) => ({
      ...current,
      [key]: Number(value.toFixed(2)),
    }));
  };

  const handleReset = () => {
    setDraftWeights({ ...DEFAULT_WEIGHTS });
  };

  const handleReload = async () => {
    await loadWeights();
    setToast({ message: 'Reloaded server-side weights.', tone: 'success' });
  };

  const handleSave = async () => {
    setSaving(true);
    try {
      await endpoints.admin.weights.update(draftWeights);
      setWeights(draftWeights);
      setToast({ message: 'Matching weights saved.', tone: 'success' });
    } catch (err) {
      setToast({ message: getErrorMessage(err), tone: 'error' });
    } finally {
      setSaving(false);
    }
  };

  return (
    <>
      <PageHeader
        title="Rules configuration"
        desc="Tune the scoring weights that decide how EOB and bill records are ranked before review."
        actions={
          <div className="btn-group">
            <Button onClick={() => void handleReload()} disabled={loading || saving}>
              Reload
            </Button>
            <Button onClick={handleReset} disabled={loading || saving}>
              Reset to defaults
            </Button>
            <Button variant="primary" onClick={() => void handleSave()} disabled={loading || saving || !canSave}>
              {saving ? 'Saving…' : 'Save weights'}
            </Button>
          </div>
        }
      />

      {toast && <Toast message={toast.message} tone={toast.tone} />}

      {loading ? (
        <LoadingState label="Loading scoring weights…" />
      ) : error ? (
        <ErrorState message={error} onRetry={() => void loadWeights()} />
      ) : (
        <>
          <StatGrid>
            <StatCard title="Configured signals" metric={Object.keys(draftWeights).length} desc="Every numeric field returned by /api/admin/weights is editable here." />
            <StatCard
              title="Weight sum"
              metric={totalWeight.toFixed(2)}
              desc="The backend expects a total near 1.00."
              status={{ label: Math.abs(totalWeight - 1) <= 0.05 ? 'Valid' : 'Needs adjustment', tone: Math.abs(totalWeight - 1) <= 0.05 ? 'success' : 'warning' }}
            />
            <StatCard title="Changed fields" metric={deltaCount} desc="Differences between the server snapshot and your current draft." />
            <StatCard title="Top signal" metric={topSignal} desc="The largest contributor to the overall matching score." />
          </StatGrid>

          <div className="section" style={{ marginTop: 20 }}>
            <Card
              title="EOB matching weights"
              actions={<Badge tone="info">Live draft</Badge>}
            >
              <div style={{ display: 'grid', gap: 16 }}>
                {Object.entries(draftWeights).map(([key, value]) => (
                  <div
                    key={key}
                    style={{
                      border: '1px solid var(--border)',
                      borderRadius: 'var(--radius-sm)',
                      padding: 16,
                      background: 'var(--bg)',
                    }}
                  >
                    <div style={{ display: 'flex', justifyContent: 'space-between', gap: 16, alignItems: 'flex-start' }}>
                      <div>
                        <div style={{ fontWeight: 700 }}>{formatLabel(key)}</div>
                        <div className="text-muted" style={{ fontSize: '0.82rem', marginTop: 4 }}>
                          {WEIGHT_DESCRIPTIONS[key] ?? 'Matching signal weight.'}
                        </div>
                      </div>
                      <div
                        style={{
                          minWidth: 64,
                          textAlign: 'center',
                          padding: '6px 10px',
                          borderRadius: 999,
                          background: 'var(--accent-bg)',
                          color: 'var(--accent)',
                          fontWeight: 700,
                        }}
                      >
                        {value.toFixed(2)}
                      </div>
                    </div>

                    <div style={{ display: 'grid', gridTemplateColumns: '1fr auto', gap: 12, alignItems: 'center', marginTop: 14 }}>
                      <input
                        aria-label={formatLabel(key)}
                        type="range"
                        min={0}
                        max={1}
                        step={0.01}
                        value={value}
                        onChange={(event) => handleSliderChange(key, Number(event.target.value))}
                        style={{ width: '100%', accentColor: 'var(--accent)' }}
                      />
                      <span className="text-muted" style={{ fontSize: '0.78rem' }}>
                        {Math.round(value * 100)}%
                      </span>
                    </div>
                  </div>
                ))}
              </div>
            </Card>
          </div>

          <div className="section">
            <Card title="How the scorer behaves">
              <div style={{ display: 'grid', gap: 14 }}>
                <div
                  style={{
                    display: 'grid',
                    gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))',
                    gap: 12,
                  }}
                >
                  <div style={{ border: '1px solid var(--border)', borderRadius: 'var(--radius-sm)', padding: 14 }}>
                    <div style={{ fontWeight: 700, marginBottom: 6 }}>Deterministic weighting</div>
                    <div className="text-muted" style={{ fontSize: '0.82rem' }}>
                      Sliders map directly to the numeric payload sent to <code>/api/admin/weights</code>.
                    </div>
                  </div>
                  <div style={{ border: '1px solid var(--border)', borderRadius: 'var(--radius-sm)', padding: 14 }}>
                    <div style={{ fontWeight: 700, marginBottom: 6 }}>Keep the sum balanced</div>
                    <div className="text-muted" style={{ fontSize: '0.82rem' }}>
                      The backend rejects drafts far from 1.00, so the save button stays disabled until the range is safe.
                    </div>
                  </div>
                  <div style={{ border: '1px solid var(--border)', borderRadius: 'var(--radius-sm)', padding: 14 }}>
                    <div style={{ fontWeight: 700, marginBottom: 6 }}>Reset when experimenting</div>
                    <div className="text-muted" style={{ fontSize: '0.82rem' }}>
                      Defaults mirror the current FastAPI admin router so you can recover quickly from an aggressive tuning pass.
                    </div>
                  </div>
                </div>

                <div
                  style={{
                    border: '1px solid var(--border)',
                    borderRadius: 'var(--radius-sm)',
                    padding: 14,
                    background: Math.abs(totalWeight - 1) <= 0.05 ? 'var(--success-bg)' : 'var(--warning-bg)',
                    color: Math.abs(totalWeight - 1) <= 0.05 ? 'var(--success)' : 'var(--warning)',
                    fontWeight: 600,
                  }}
                >
                  Current draft total: {totalWeight.toFixed(2)}
                  <span style={{ color: 'var(--text-muted)', fontWeight: 500 }}> · Save succeeds when the sum stays within ±0.05 of 1.00.</span>
                </div>
              </div>
            </Card>
          </div>
        </>
      )}
    </>
  );
}

