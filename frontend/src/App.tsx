import { HashRouter, Navigate, Route, Routes, useParams } from 'react-router-dom';
import { TopNav } from './components/TopNav';
import { TooltipProvider } from './components/ui';
import OverviewDashboard from './pages/OverviewDashboard';
import DashboardView from './pages/DashboardView';
import CorrectionHistory from './pages/CorrectionHistory';
import Statements from './pages/Statements';
import StatementSeriesDetail from './pages/StatementSeriesDetail';
import CorrespondentReview from './pages/CorrespondentReview';
import EobWorkspace from './pages/EobWorkspace';
import ActionQueue from './pages/ActionQueue';
import ActionQueueOperations from './pages/ActionQueueOperations';
import TriageQueue from './pages/TriageQueue';
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
import OcrQualityReviewQueue from './pages/OcrQualityReviewQueue';
import OcrQualityDocumentDetail from './pages/OcrQualityDocumentDetail';

/** Redirect legacy /eob/matches/:matchId to the unified workspace. */
function EobMatchRedirect() {
  const { matchId } = useParams();
  return <Navigate to={`/eob?tab=review&matchId=${matchId}`} replace />;
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
            <Route path="/statements" element={<Statements />} />
            <Route path="/statements/:seriesId" element={<StatementSeriesDetail />} />
            <Route path="/correspondents" element={<CorrespondentReview />} />
            <Route path="/correspondents/:correspondentId" element={<CorrespondentReview />} />
            <Route path="/eob" element={<EobWorkspace />} />
            {/* Legacy routes redirect into the unified workspace */}
            <Route path="/eob/matches/:matchId" element={<EobMatchRedirect />} />
            <Route path="/eob/unmatched" element={<Navigate to="/eob?tab=unmatched" replace />} />
            <Route path="/eob/benchmarks" element={<BenchmarkHistory />} />
            <Route path="/eob/manual-search" element={<ManualMatchSearch />} />
            <Route path="/action-queue" element={<ActionQueue />} />
            <Route path="/action-queue/operations" element={<ActionQueueOperations />} />
            <Route path="/triage" element={<TriageQueue />} />
            <Route path="/triage/manual-search" element={<ManualMatchSearch />} />
            <Route path="/metadata/:docId" element={<MetadataCorrection />} />
            <Route path="/rules" element={<RulesConfig />} />
            <Route path="/insights" element={<Insights />} />
            <Route path="/ocr-quality" element={<OcrQualityDashboard />} />
            <Route path="/ocr-quality/queue" element={<OcrQualityReviewQueue />} />
            <Route path="/ocr-quality/documents/:documentId" element={<OcrQualityDocumentDetail />} />
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

export default App;
