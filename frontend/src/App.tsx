import { HashRouter, Route, Routes } from 'react-router-dom';
import { TopNav } from './components/TopNav';
import OverviewDashboard from './pages/OverviewDashboard';
import DashboardView from './pages/DashboardView';
import CorrectionHistory from './pages/CorrectionHistory';
import Statements from './pages/Statements';
import StatementSeriesDetail from './pages/StatementSeriesDetail';
import EobDashboard from './pages/EobDashboard';
import EobMatchReview from './pages/EobMatchReview';
import EobUnmatched from './pages/EobUnmatched';
import ActionQueue from './pages/ActionQueue';
import TriageQueue from './pages/TriageQueue';
import MetadataCorrection from './pages/MetadataCorrection';
import ManualMatchSearch from './pages/ManualMatchSearch';
import RulesConfig from './pages/RulesConfig';
import Insights from './pages/Insights';
import History from './pages/History';
import OrphansDupes from './pages/OrphansDupes';
import Settings from './pages/Settings';
import NotFound from './pages/NotFound';

function App() {
  return (
    <HashRouter>
      <div className="app-shell">
        <TopNav />
        <main className="app-main">
          <Routes>
            <Route path="/" element={<OverviewDashboard />} />
            <Route path="/dashboard-view" element={<DashboardView />} />
            <Route path="/corrections" element={<CorrectionHistory />} />
            <Route path="/statements" element={<Statements />} />
            <Route path="/statements/:seriesId" element={<StatementSeriesDetail />} />
            <Route path="/eob" element={<EobDashboard />} />
            <Route path="/eob/matches/:matchId" element={<EobMatchReview />} />
            <Route path="/eob/unmatched" element={<EobUnmatched />} />
            <Route path="/action-queue" element={<ActionQueue />} />
            <Route path="/triage" element={<TriageQueue />} />
            <Route path="/triage/manual-search" element={<ManualMatchSearch />} />
            <Route path="/metadata/:docId" element={<MetadataCorrection />} />
            <Route path="/rules" element={<RulesConfig />} />
            <Route path="/insights" element={<Insights />} />
            <Route path="/history" element={<History />} />
            <Route path="/orphans" element={<OrphansDupes />} />
            <Route path="/settings" element={<Settings />} />
            <Route path="*" element={<NotFound />} />
          </Routes>
        </main>
      </div>
    </HashRouter>
  );
}

export default App;
