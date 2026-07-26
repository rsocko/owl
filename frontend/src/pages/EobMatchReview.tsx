import { useCallback } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { Breadcrumb, PageHeader } from '../components/ui';
import EobMatchDetail from '../components/EobMatchDetail';
import '../styles/eob-pages.css';

/**
 * Standalone page wrapper for EobMatchDetail.
 *
 * Renders the full match review experience with a page header and
 * navigation back to the EOB dashboard. The actual review logic is
 * in the reusable EobMatchDetail component (also used inline by the
 * triage queue).
 */
export default function EobMatchReview() {
  const navigate = useNavigate();
  const { matchId } = useParams();
  const numericMatchId = Number(matchId);

  const handleSkip = useCallback(() => {
    navigate('/eob');
  }, [navigate]);

  if (!matchId || Number.isNaN(numericMatchId)) {
    return (
      <>
        <Breadcrumb
          items={[
            { label: 'EOB Matching', to: '/eob' },
            { label: 'Match Review' },
          ]}
        />
        <PageHeader
          title="Match review"
          desc="Invalid match ID."
        />
      </>
    );
  }

  return (
    <>
      <Breadcrumb
        items={[
          { label: 'EOB Matching', to: '/eob' },
          { label: 'Match Review' },
        ]}
      />
      <PageHeader
        title={
          <div className="eob-header-stack">
            <Link to="/eob" className="eob-link">
              ← Back to dashboard
            </Link>
            <span>Match review</span>
          </div>
        }
        desc="Inspect the candidate pair and decide whether it should be confirmed."
      />

      <EobMatchDetail
        matchId={numericMatchId}
        onSkip={handleSkip}
      />
    </>
  );
}
