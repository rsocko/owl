import { useEffect, useState } from 'react';
import { HashRouter, Navigate, Route, Routes, useLocation, useParams } from 'react-router-dom';
import { TopNav } from './components/TopNav';
import { TooltipProvider } from './components/ui';
import OverviewDashboard from './pages/OverviewDashboard';
import DashboardView from './pages/DashboardView';
import CorrectionHistory from './pages/CorrectionHistory';
import DocumentCoverage from './pages/DocumentCoverage';
import DocumentSeriesDetail from './pages/DocumentSeriesDetail';
import DocumentSources from './pages/DocumentSources';
import ExpectationSetup from './pages/ExpectationSetup';
import EobWorkspace from './pages/EobWorkspace';
import DocumentWorkspace from './pages/DocumentWorkspace';
import ActionQueueOperations from './pages/ActionQueueOperations';
import MetadataCorrection from './pages/MetadataCorrection';
import ManualMatchSearch from './pages/ManualMatchSearch';
import RulesConfig from './pages/RulesConfig';
import Insights from './pages/Insights';
import BenchmarkHistory from './pages/BenchmarkHistory';
import History from './pages/History';
import OrphansDupes from './pages/OrphansDupes';
import Settings from './pages/Settings';
import NotFound from './pages/NotFound';
import DocumentViews from './pages/DocumentViews';
import OcrQualityDashboard from './pages/OcrQualityDashboard';
import OcrFocusInspectorPage from './pages/OcrFocusInspectorPage';
import { endpoints } from './lib/api';
import { canonicalActionTarget, type CanonicalActionLocation } from './lib/canonicalAction';
import { legacyOcrQueueTarget } from './lib/ocrNavigation';
import { legacySourceTarget } from './lib/routeTargets';

/** Redirect legacy /eob/matches/:matchId to the unified workspace. */
function EobMatchRedirect() {
  const { matchId } = useParams();
  return <Navigate to={`/eob?tab=review&matchId=${matchId}`} replace />;
}

function LegacyRouteRedirect({ to }: { to: string }) {
  const { search } = useLocation();
  return <Navigate to={`${to}${search}`} replace />;
}

export function LegacyStatementsRedirect() {
  return <Navigate to="/coverage?kind=statement" replace />;
}

function LegacySourceRedirect() {
  const { correspondentId } = useParams();
  const { search } = useLocation();
  return <Navigate to={legacySourceTarget(correspondentId, search)} replace />;
}

function LegacyInboxRedirect() {
  const { documentId } = useParams();
  const { search } = useLocation();
  const params = new URLSearchParams(search);
  if (documentId && !params.has('document')) params.set('document', documentId);
  const query = params.toString();
  return <Navigate to={`/inbox${query ? `?${query}` : ''}`} replace />;
}

function LegacyOcrQueueRedirect() {
  const { search } = useLocation();
  return <Navigate to={legacyOcrQueueTarget(search)} replace />;
}

function CanonicalActionRedirect() {
  const { actionId } = useParams();
  const [target, setTarget] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    if (!actionId) {
      setFailed(true);
      return;
    }
    void endpoints.actionQueue.action(actionId)
      .then((response) => {
        const action = response as CanonicalActionLocation;
        setTarget(canonicalActionTarget(action));
      })
      .catch(() => setFailed(true));
  }, [actionId]);

  if (target) return <Navigate to={target} replace />;
  if (failed) return <Navigate to="/actions" replace />;
  return <div role="status">Opening action…</div>;
}

function App() {
  return (
    <HashRouter>
      <TooltipProvider>
      <div className="app-shell">
        <TopNav />
        <main className="app-main">
          <Routes>
            <Route path="/" element={<OverviewDashboard />} />
            <Route path="/dashboard-view" element={<DashboardView />} />
            <Route path="/document-views" element={<DocumentViews />} />
            <Route path="/corrections" element={<CorrectionHistory />} />
            <Route path="/coverage" element={<DocumentCoverage />} />
            <Route path="/coverage/series/:seriesId" element={<DocumentSeriesDetail />} />
            <Route path="/statements" element={<LegacyStatementsRedirect />} />
            <Route path="/statements/:seriesId" element={<LegacyStatementSeriesRedirect />} />
            <Route path="/sources" element={<DocumentSources />} />
            <Route path="/sources/:correspondentId" element={<DocumentSources />} />
            <Route path="/sources/:correspondentId/expectations/new" element={<ExpectationSetup />} />
            <Route path="/correspondents" element={<LegacySourceRedirect />} />
            <Route path="/correspondents/:correspondentId" element={<LegacySourceRedirect />} />
            <Route path="/eob" element={<EobWorkspace />} />
            {/* Legacy routes redirect into the unified workspace */}
            <Route path="/eob/matches/:matchId" element={<EobMatchRedirect />} />
            <Route path="/eob/unmatched" element={<Navigate to="/eob?tab=unmatched" replace />} />
            <Route path="/eob/benchmarks" element={<BenchmarkHistory />} />
            <Route path="/eob/manual-search" element={<ManualMatchSearch />} />
            <Route path="/inbox" element={<DocumentWorkspace mode="inbox" />} />
            <Route path="/inbox/:documentId" element={<DocumentWorkspace mode="inbox" />} />
            <Route path="/actions" element={<DocumentWorkspace mode="actions" />} />
            <Route path="/actions/operations" element={<ActionQueueOperations />} />
            <Route path="/actions/:actionId" element={<CanonicalActionRedirect />} />
            <Route path="/needs-review" element={<DocumentWorkspace mode="review" />} />
            <Route path="/inbox-review" element={<LegacyRouteRedirect to="/inbox" />} />
            <Route path="/inbox-review/:documentId" element={<LegacyInboxRedirect />} />
            <Route path="/intake-review" element={<LegacyInboxRedirect />} />
            <Route path="/intake-review/:documentId" element={<LegacyInboxRedirect />} />
            <Route path="/action-queue" element={<LegacyRouteRedirect to="/actions" />} />
            <Route path="/action-queue/operations" element={<LegacyRouteRedirect to="/actions/operations" />} />
            <Route path="/needs-review/manual-search" element={<ManualMatchSearch />} />
            <Route path="/triage" element={<LegacyRouteRedirect to="/needs-review" />} />
            <Route
              path="/triage/manual-search"
              element={<LegacyRouteRedirect to="/needs-review/manual-search" />}
            />
            <Route path="/metadata/:docId" element={<MetadataCorrection />} />
            <Route path="/rules" element={<RulesConfig />} />
            <Route path="/insights" element={<Insights />} />
            <Route path="/ocr-quality" element={<OcrQualityDashboard />} />
            <Route path="/ocr-quality/queue" element={<LegacyOcrQueueRedirect />} />
            <Route path="/ocr-quality/documents/:documentId" element={<OcrFocusInspectorPage />} />
            <Route path="/history" element={<History />} />
            <Route path="/orphans" element={<OrphansDupes />} />
            <Route path="/settings" element={<Settings />} />
            <Route path="*" element={<NotFound />} />
          </Routes>
        </main>
      </div>
      </TooltipProvider>
    </HashRouter>
  );
}

export function LegacyStatementSeriesRedirect() {
  const { seriesId } = useParams();
  return <Navigate to={`/coverage/series/${encodeURIComponent(seriesId ?? '')}`} replace />;
}

export default App;
