import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import Insights, {
  formatCurrency,
  formatDate,
  changePctLabel,
  insightIcon,
  insightTypeTab,
  alertToInsight,
  getErrorMessage,
} from './Insights';
import type { AlertItem } from './Insights';

/* ================================================================
 * Unit tests for pure helper functions
 * ================================================================ */

describe('getErrorMessage', () => {
  it('extracts message from Error instances', () => {
    expect(getErrorMessage(new Error('boom'))).toBe('boom');
  });
  it('returns fallback for non-Error values', () => {
    expect(getErrorMessage('oops')).toBe('Something went wrong.');
    expect(getErrorMessage(null)).toBe('Something went wrong.');
    expect(getErrorMessage(42)).toBe('Something went wrong.');
  });
});

describe('formatDate', () => {
  it('returns em-dash for null/undefined', () => {
    expect(formatDate(null)).toBe('—');
    expect(formatDate(undefined)).toBe('—');
    expect(formatDate('')).toBe('—');
  });
  it('formats valid ISO dates', () => {
    const result = formatDate('2024-07-18T00:00:00Z');
    // Should contain "Jul" and "2024" in some locale-dependent format
    expect(result).toContain('Jul');
    expect(result).toContain('2024');
  });
  it('returns raw string for unparseable dates', () => {
    expect(formatDate('not-a-date')).toBe('not-a-date');
  });
});

describe('formatCurrency', () => {
  it('returns em-dash for null/undefined', () => {
    expect(formatCurrency(null)).toBe('—');
    expect(formatCurrency(undefined)).toBe('—');
  });
  it('formats positive values as USD', () => {
    const result = formatCurrency(2847);
    expect(result).toContain('2,847');
    expect(result).toContain('$');
  });
  it('formats zero correctly', () => {
    const result = formatCurrency(0);
    expect(result).toContain('$');
    expect(result).toContain('0');
  });
  it('formats negative values', () => {
    const result = formatCurrency(-45.5);
    expect(result).toContain('45.5');
  });
  it('formats decimal values with max 2 fraction digits', () => {
    const result = formatCurrency(129.99);
    expect(result).toContain('129.99');
  });
});

describe('changePctLabel', () => {
  it('returns flat for null/undefined', () => {
    expect(changePctLabel(null)).toEqual({ text: '— 0%', tone: 'flat' });
    expect(changePctLabel(undefined)).toEqual({ text: '— 0%', tone: 'flat' });
  });
  it('returns flat for small changes (<1%)', () => {
    expect(changePctLabel(0.5)).toEqual({ text: '— 0%', tone: 'flat' });
    expect(changePctLabel(-0.3)).toEqual({ text: '— 0%', tone: 'flat' });
  });
  it('returns up tone for positive changes', () => {
    const result = changePctLabel(40);
    expect(result.tone).toBe('up');
    expect(result.text).toContain('▲');
    expect(result.text).toContain('+40%');
  });
  it('returns down tone for negative changes', () => {
    const result = changePctLabel(-25);
    expect(result.tone).toBe('down');
    expect(result.text).toContain('▼');
    expect(result.text).toContain('-25%');
  });
  it('rounds non-integer percentages', () => {
    const result = changePctLabel(12.7);
    expect(result.text).toContain('13%');
  });
});

describe('insightIcon', () => {
  it('maps spend_summary and anomaly to chart icon', () => {
    expect(insightIcon('spend_summary')).toBe('📊');
    expect(insightIcon('anomaly')).toBe('📊');
  });
  it('maps trend to trend icon', () => {
    expect(insightIcon('trend')).toBe('📈');
  });
  it('maps new_category to new icon', () => {
    expect(insightIcon('new_category')).toBe('🆕');
  });
  it('maps compliance to check icon', () => {
    expect(insightIcon('compliance')).toBe('✅');
  });
  it('returns default for unknown/null', () => {
    expect(insightIcon(null)).toBe('💡');
    expect(insightIcon(undefined)).toBe('💡');
    expect(insightIcon('unknown_type')).toBe('💡');
  });
});

describe('insightTypeTab', () => {
  it('maps spend types to anomalies tab', () => {
    expect(insightTypeTab('spend_summary')).toBe('anomalies');
    expect(insightTypeTab('anomaly')).toBe('anomalies');
    expect(insightTypeTab('new_category')).toBe('anomalies');
  });
  it('maps trend to trends tab', () => {
    expect(insightTypeTab('trend')).toBe('trends');
  });
  it('maps compliance to compliance tab', () => {
    expect(insightTypeTab('compliance')).toBe('compliance');
  });
  it('defaults unknown types to anomalies', () => {
    expect(insightTypeTab(null)).toBe('anomalies');
    expect(insightTypeTab('alert')).toBe('anomalies');
  });
});

describe('alertToInsight', () => {
  const baseAlert: AlertItem = {
    id: 42,
    alert_type: 'missing_statement',
    severity: 'high',
    module: 'statements',
    title: 'Missing bill',
    description: 'Expected statement not received',
    created_at: '2024-07-01T00:00:00Z',
    acknowledged_at: null,
    resolved_at: null,
    metadata: { key: 'value' },
  };

  it('converts a new (unacknowledged, unresolved) alert', () => {
    const result = alertToInsight(baseAlert);
    expect(result.id).toBe(42);
    expect(result.insight_type).toBe('missing_statement');
    expect(result.status).toBe('new');
    expect(result.title).toBe('Missing bill');
    expect(result.generated_at).toBe('2024-07-01T00:00:00Z');
    expect(result.evidence).toBeNull();
  });

  it('converts an acknowledged alert to viewed status', () => {
    const acked = { ...baseAlert, acknowledged_at: '2024-07-02T00:00:00Z' };
    const result = alertToInsight(acked);
    expect(result.status).toBe('viewed');
    expect(result.acknowledged_at).toBe('2024-07-02T00:00:00Z');
  });

  it('converts a resolved alert to archived status', () => {
    const resolved = {
      ...baseAlert,
      acknowledged_at: '2024-07-02T00:00:00Z',
      resolved_at: '2024-07-03T00:00:00Z',
    };
    const result = alertToInsight(resolved);
    expect(result.status).toBe('archived');
    expect(result.archived_at).toBe('2024-07-03T00:00:00Z');
  });

  it('falls back to module when alert_type is null', () => {
    const noType = { ...baseAlert, alert_type: null };
    const result = alertToInsight(noType);
    expect(result.insight_type).toBe('statements');
  });

  it('falls back to "alert" when both alert_type and module are null', () => {
    const noTypeNoModule = { ...baseAlert, alert_type: null, module: null };
    const result = alertToInsight(noTypeNoModule);
    expect(result.insight_type).toBe('alert');
  });
});

/* ================================================================
 * Component rendering tests
 * ================================================================ */

// Mock the API endpoints
vi.mock('../lib/api', () => ({
  endpoints: {
    insights: {
      summary: vi.fn(),
      list: vi.fn(),
      acknowledge: vi.fn(),
      archive: vi.fn(),
    },
    alerts: {
      summary: vi.fn(),
      list: vi.fn(),
      acknowledge: vi.fn(),
      resolve: vi.fn(),
    },
    statements: {
      series: vi.fn(),
    },
  },
}));

import { endpoints } from '../lib/api';

const mockInsightsSummary = endpoints.insights.summary as ReturnType<typeof vi.fn>;
const mockInsightsList = endpoints.insights.list as ReturnType<typeof vi.fn>;
const mockStatementsSeries = endpoints.statements.series as ReturnType<typeof vi.fn>;

function renderInsights() {
  return render(
    <MemoryRouter>
      <Insights />
    </MemoryRouter>,
  );
}

describe('Insights page component', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    // Default: series endpoint returns empty list
    mockStatementsSeries.mockResolvedValue([]);
  });

  it('shows loading skeleton initially', () => {
    // Never resolve the promises — keeps page in loading state
    mockInsightsSummary.mockReturnValue(new Promise(() => {}));
    mockInsightsList.mockReturnValue(new Promise(() => {}));

    renderInsights();
    // Skeleton loaders render stat-card placeholders
    expect(document.querySelector('.skeleton-stat-card')).toBeTruthy();
  });

  it('shows error state when both APIs fail', async () => {
    mockInsightsSummary.mockRejectedValue(new Error('Network error'));
    mockInsightsList.mockRejectedValue(new Error('Network error'));

    // Also mock fallback alerts to fail
    const mockAlertsSummary = endpoints.alerts.summary as ReturnType<typeof vi.fn>;
    const mockAlertsList = endpoints.alerts.list as ReturnType<typeof vi.fn>;
    mockAlertsSummary.mockRejectedValue(new Error('Network error'));
    mockAlertsList.mockRejectedValue(new Error('Network error'));

    renderInsights();

    await waitFor(() => {
      expect(screen.getByText('Network error')).toBeInTheDocument();
    });
    expect(screen.getByText('Retry')).toBeInTheDocument();
  });

  it('renders stat cards and filter tabs with real data', async () => {
    mockInsightsSummary.mockResolvedValue({
      total: 8,
      new_count: 3,
      by_severity: { critical: 1, high: 2, medium: 3, low: 2 },
      by_type: { spend_summary: 4, trend: 2, compliance: 2 },
    });
    mockInsightsList.mockResolvedValue({
      insights: [
        { id: '1', insight_type: 'spend_summary', status: 'new', title: 'Chase Sapphire — June Spend', generated_at: '2024-07-18' },
        { id: '2', insight_type: 'trend', status: 'viewed', title: 'AT&T — Bill Trend', generated_at: '2024-07-15', acknowledged_at: '2024-07-16' },
        { id: '3', insight_type: 'compliance', status: 'new', title: 'June Statements — All Received', generated_at: '2024-07-01' },
      ],
      total: 3,
    });

    renderInsights();

    await waitFor(() => {
      expect(screen.getByText('Total insights')).toBeInTheDocument();
    });

    // Stat cards rendered
    expect(screen.getByText('8')).toBeInTheDocument(); // total
    // "3" appears in both New and Critical+High stat cards
    expect(screen.getAllByText('3').length).toBeGreaterThanOrEqual(2);

    // Filter tabs rendered with counts
    expect(screen.getByText(/All.*3/)).toBeInTheDocument();

    // Insight card titles
    expect(screen.getByText('Chase Sapphire — June Spend')).toBeInTheDocument();
    expect(screen.getByText('AT&T — Bill Trend')).toBeInTheDocument();
    expect(screen.getByText('June Statements — All Received')).toBeInTheDocument();
  });

  it('falls back to alerts API when insights API fails', async () => {
    mockInsightsSummary.mockRejectedValue(new Error('404'));
    mockInsightsList.mockRejectedValue(new Error('404'));

    const mockAlertsSummary = endpoints.alerts.summary as ReturnType<typeof vi.fn>;
    const mockAlertsList = endpoints.alerts.list as ReturnType<typeof vi.fn>;
    mockAlertsSummary.mockResolvedValue({
      total: 2,
      unacknowledged: 1,
      by_severity: { high: 1, medium: 1 },
      by_module: { statements: 1, eob: 1 },
    });
    mockAlertsList.mockResolvedValue({
      alerts: [
        { id: 1, title: 'Alert from fallback', severity: 'high', module: 'statements', created_at: '2024-07-01' },
      ],
      total: 1,
    });

    renderInsights();

    await waitFor(() => {
      expect(screen.getByText('Alert from fallback')).toBeInTheDocument();
    });
    expect(screen.getByText(/Using alert feed/)).toBeInTheDocument();
  });

  it('filters by tab when clicked', async () => {
    mockInsightsSummary.mockResolvedValue({ total: 3 });
    mockInsightsList.mockResolvedValue({
      insights: [
        { id: '1', insight_type: 'spend_summary', status: 'new', title: 'Spend Insight' },
        { id: '2', insight_type: 'trend', status: 'new', title: 'Trend Insight' },
        { id: '3', insight_type: 'compliance', status: 'new', title: 'Compliance Insight' },
      ],
      total: 3,
    });

    renderInsights();

    await waitFor(() => {
      expect(screen.getByText('Spend Insight')).toBeInTheDocument();
    });

    // Click on Trends tab
    fireEvent.click(screen.getByText(/Trends.*1/));

    // Should only show trend insight
    expect(screen.getByText('Trend Insight')).toBeInTheDocument();
    expect(screen.queryByText('Spend Insight')).not.toBeInTheDocument();
    expect(screen.queryByText('Compliance Insight')).not.toBeInTheDocument();
  });

  it('filters by search text', async () => {
    mockInsightsSummary.mockResolvedValue({ total: 2 });
    mockInsightsList.mockResolvedValue({
      insights: [
        { id: '1', insight_type: 'spend_summary', status: 'new', title: 'Chase Card Spend' },
        { id: '2', insight_type: 'trend', status: 'new', title: 'Comcast Bill Trend' },
      ],
      total: 2,
    });

    renderInsights();

    await waitFor(() => {
      expect(screen.getByText('Chase Card Spend')).toBeInTheDocument();
    });

    const searchInput = screen.getByPlaceholderText('🔍 Search insights...');
    fireEvent.change(searchInput, { target: { value: 'comcast' } });

    expect(screen.queryByText('Chase Card Spend')).not.toBeInTheDocument();
    expect(screen.getByText('Comcast Bill Trend')).toBeInTheDocument();
  });

  it('groups insights by status with section dividers', async () => {
    mockInsightsSummary.mockResolvedValue({ total: 3 });
    mockInsightsList.mockResolvedValue({
      insights: [
        { id: '1', status: 'new', title: 'New Insight', insight_type: 'anomaly' },
        { id: '2', status: 'viewed', title: 'Viewed Insight', insight_type: 'trend', acknowledged_at: '2024-01-01' },
        { id: '3', status: 'archived', title: 'Archived Insight', insight_type: 'compliance', archived_at: '2024-01-01' },
      ],
      total: 3,
    });

    renderInsights();

    await waitFor(() => {
      expect(screen.getByText('New Insight')).toBeInTheDocument();
    });

    // Section dividers
    expect(screen.getByText('New (1)')).toBeInTheDocument();
    expect(screen.getByText('Viewed (1)')).toBeInTheDocument();
    expect(screen.getByText('Archived (1)')).toBeInTheDocument();
  });

  it('shows empty state when no insights match filters', async () => {
    mockInsightsSummary.mockResolvedValue({ total: 0 });
    mockInsightsList.mockResolvedValue({ insights: [], total: 0 });

    renderInsights();

    await waitFor(() => {
      expect(screen.getByText('No insights match the current filters.')).toBeInTheDocument();
    });
  });

  it('renders series filter dropdown with options from API', async () => {
    mockStatementsSeries.mockResolvedValue([
      { id: '1', name: 'Chase Sapphire', account_identifier: 'chase-sapphire' },
      { id: '2', name: 'Comcast', account_identifier: 'comcast' },
    ]);
    mockInsightsSummary.mockResolvedValue({ total: 0 });
    mockInsightsList.mockResolvedValue({ insights: [], total: 0 });

    renderInsights();

    await waitFor(() => {
      expect(screen.getByLabelText('Filter by series')).toBeInTheDocument();
    });

    const seriesSelect = screen.getByLabelText('Filter by series') as HTMLSelectElement;
    expect(seriesSelect.value).toBe('');

    // Verify options rendered
    const options = seriesSelect.querySelectorAll('option');
    expect(options.length).toBe(3); // "All Series" + 2 series
    expect(options[0].textContent).toBe('All Series');
    expect(options[1].textContent).toBe('Chase Sapphire');
    expect(options[2].textContent).toBe('Comcast');
  });

  it('passes series_id to insights API when a series is selected', async () => {
    mockStatementsSeries.mockResolvedValue([
      { id: '42', name: 'Chase Sapphire' },
    ]);
    mockInsightsSummary.mockResolvedValue({ total: 1 });
    mockInsightsList.mockResolvedValue({
      insights: [{ id: '1', insight_type: 'spend_summary', status: 'new', title: 'Spend' }],
      total: 1,
    });

    renderInsights();

    await waitFor(() => {
      expect(screen.getByLabelText('Filter by series')).toBeInTheDocument();
    });

    // Wait for initial load to finish
    await waitFor(() => {
      expect(screen.getByText('Spend')).toBeInTheDocument();
    });

    // Clear mocks to isolate the re-fetch triggered by series change
    mockInsightsSummary.mockClear();
    mockInsightsList.mockClear();
    mockInsightsSummary.mockResolvedValue({ total: 1 });
    mockInsightsList.mockResolvedValue({ insights: [], total: 0 });

    const seriesSelect = screen.getByLabelText('Filter by series');
    fireEvent.change(seriesSelect, { target: { value: '42' } });

    await waitFor(() => {
      expect(mockInsightsList).toHaveBeenCalled();
    });

    // Verify that series_id was included in the API call
    const callArg = mockInsightsList.mock.calls[0][0] as string;
    expect(callArg).toContain('series_id=42');
  });

  it('renders series dropdown with "All Series" default when API fails', async () => {
    mockStatementsSeries.mockRejectedValue(new Error('Network error'));
    mockInsightsSummary.mockResolvedValue({ total: 0 });
    mockInsightsList.mockResolvedValue({ insights: [], total: 0 });

    renderInsights();

    await waitFor(() => {
      expect(screen.getByLabelText('Filter by series')).toBeInTheDocument();
    });

    const seriesSelect = screen.getByLabelText('Filter by series') as HTMLSelectElement;
    // Only "All Series" option
    const options = seriesSelect.querySelectorAll('option');
    expect(options.length).toBe(1);
    expect(options[0].textContent).toBe('All Series');
  });

  it('handles series response wrapped in { series: [...] } format', async () => {
    mockStatementsSeries.mockResolvedValue({
      series: [
        { id: '10', name: 'AT&T Wireless' },
      ],
    });
    mockInsightsSummary.mockResolvedValue({ total: 0 });
    mockInsightsList.mockResolvedValue({ insights: [], total: 0 });

    renderInsights();

    await waitFor(() => {
      const seriesSelect = screen.getByLabelText('Filter by series') as HTMLSelectElement;
      const options = seriesSelect.querySelectorAll('option');
      expect(options.length).toBe(2);
      expect(options[1].textContent).toBe('AT&T Wireless');
    });
  });

  it('falls back to account_identifier when series name is null', async () => {
    mockStatementsSeries.mockResolvedValue([
      { id: '5', name: null, account_identifier: 'acct-123' },
    ]);
    mockInsightsSummary.mockResolvedValue({ total: 0 });
    mockInsightsList.mockResolvedValue({ insights: [], total: 0 });

    renderInsights();

    await waitFor(() => {
      const seriesSelect = screen.getByLabelText('Filter by series') as HTMLSelectElement;
      const options = seriesSelect.querySelectorAll('option');
      expect(options[1].textContent).toBe('acct-123');
    });
  });
});
