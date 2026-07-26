/**
 * EobWorkspace — unified EOB matching workspace (UX-01).
 *
 * Consolidates the previously separate EobDashboard, EobUnmatched, and
 * EobMatchReview pages into a single tabbed workspace with persistent
 * breadcrumb navigation and deep-linking via URL search params.
 *
 * Tab structure:
 *   - Dashboard: stats, runs, recent matches, coverage, alerts
 *   - Unmatched: full unmatched document queue with filters
 *   - Review: match detail review panel (when a matchId is selected)
 *
 * URL params:
 *   - ?tab=dashboard | unmatched | review
 *   - ?matchId=123 (auto-selects Review tab)
 */

import { useCallback, useEffect, useMemo, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import {
  Badge,
  Breadcrumb,
  PageHeader,
  Tabs,
} from '../components/ui';
import EobDashboardTab from './EobDashboard';
import EobUnmatchedTab from './EobUnmatched';
import EobMatchDetail from '../components/EobMatchDetail';
import '../styles/eob-pages.css';

type WorkspaceTab = 'dashboard' | 'unmatched' | 'review';

const TAB_LABELS: Record<WorkspaceTab, string> = {
  dashboard: 'Dashboard',
  unmatched: 'Unmatched Queue',
  review: 'Match Review',
};

function isValidTab(value: string | null): value is WorkspaceTab {
  return value === 'dashboard' || value === 'unmatched' || value === 'review';
}

export default function EobWorkspace() {
  const [searchParams, setSearchParams] = useSearchParams();

  const matchIdParam = searchParams.get('matchId');
  const tabParam = searchParams.get('tab');

  // Derive active tab from URL
  const activeTab: WorkspaceTab = useMemo(() => {
    if (matchIdParam) return 'review';
    if (isValidTab(tabParam)) return tabParam;
    return 'dashboard';
  }, [tabParam, matchIdParam]);

  const [reviewMatchId, setReviewMatchId] = useState<number | null>(
    matchIdParam ? Number(matchIdParam) : null,
  );

  // Sync matchId state when URL param changes
  useEffect(() => {
    if (matchIdParam) {
      setReviewMatchId(Number(matchIdParam));
    } else {
      setReviewMatchId(null);
    }
  }, [matchIdParam]);

  const setTab = useCallback(
    (tab: WorkspaceTab) => {
      const next = new URLSearchParams(searchParams);
      next.set('tab', tab);
      if (tab !== 'review') {
        next.delete('matchId');
      }
      setSearchParams(next, { replace: true });
    },
    [searchParams, setSearchParams],
  );

  /** Navigate to match review from any tab. */
  const openMatchReview = useCallback(
    (matchId: number) => {
      setReviewMatchId(matchId);
      const next = new URLSearchParams(searchParams);
      next.set('tab', 'review');
      next.set('matchId', String(matchId));
      setSearchParams(next, { replace: true });
    },
    [searchParams, setSearchParams],
  );

  const handleReviewSkip = useCallback(() => {
    setTab('dashboard');
  }, [setTab]);

  const numericMatchId = reviewMatchId ?? 0;
  const hasValidReviewId = reviewMatchId !== null && !Number.isNaN(numericMatchId) && numericMatchId > 0;

  return (
    <div className="eob-workspace-tabs">
      <Breadcrumb
        items={[
          { label: 'Home', to: '/' },
          { label: 'EOB Matching', to: '/eob' },
          ...(activeTab !== 'dashboard'
            ? [{ label: TAB_LABELS[activeTab] }]
            : []),
        ]}
      />

      <PageHeader
        title="EOB Matching"
        desc="Unified workspace for matching, reviewing, and resolving Explanation of Benefits documents."
      />

      <Tabs
        active={activeTab}
        onChange={(key) => setTab(key as WorkspaceTab)}
        tabs={[
          { key: 'dashboard', label: '📊 Dashboard' },
          { key: 'unmatched', label: '📋 Unmatched Queue' },
          {
            key: 'review',
            label: (
              <>
                🔍 Match Review
                {hasValidReviewId && (
                  <Badge tone="muted">#{reviewMatchId}</Badge>
                )}
              </>
            ),
          },
        ]}
      />

      {/* Tab content */}
      {activeTab === 'dashboard' && (
        <EobDashboardTab
          embedded
          onNavigateMatch={openMatchReview}
          onNavigateUnmatched={() => setTab('unmatched')}
        />
      )}

      {activeTab === 'unmatched' && (
        <EobUnmatchedTab
          embedded
          onNavigateMatch={openMatchReview}
        />
      )}

      {activeTab === 'review' && (
        hasValidReviewId ? (
          <EobMatchDetail
            matchId={numericMatchId}
            onSkip={handleReviewSkip}
          />
        ) : (
          <div className="eob-page-stack">
            <div className="eob-tip-box">
              <span className="eob-tip-icon">🔍</span>
              <div>
                <strong>No match selected.</strong> Go to the{' '}
                <button
                  className="eob-link"
                  style={{ background: 'none', border: 'none', cursor: 'pointer', padding: 0 }}
                  onClick={() => setTab('dashboard')}
                >
                  Dashboard
                </button>{' '}
                or{' '}
                <button
                  className="eob-link"
                  style={{ background: 'none', border: 'none', cursor: 'pointer', padding: 0 }}
                  onClick={() => setTab('unmatched')}
                >
                  Unmatched Queue
                </button>{' '}
                and select a match to review.
              </div>
            </div>
          </div>
        )
      )}
    </div>
  );
}
