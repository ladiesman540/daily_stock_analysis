import type React from 'react';
import { useEffect } from 'react';
import { BrowserRouter as Router, Navigate, Route, Routes, useLocation } from 'react-router-dom';
import HomePage from './pages/HomePage';
import TodayPage from './pages/TodayPage';
import BacktestPage from './pages/BacktestPage';
import ScorecardPage from './pages/ScorecardPage';
import SettingsPage from './pages/SettingsPage';
import LoginPage from './pages/LoginPage';
import NotFoundPage from './pages/NotFoundPage';
import ChatPage from './pages/ChatPage';
import PortfolioPage from './pages/PortfolioPage';
import SignalsPage from './pages/SignalsPage';
import RotationPage from './pages/RotationPage';
import SourcesPage from './pages/SourcesPage';
import PositioningPage from './pages/PositioningPage';
import GuidePage from './pages/GuidePage';
import DataPage from './pages/DataPage';
import { ApiErrorAlert, Shell } from './components/common';
import { AuthProvider, useAuth } from './contexts/AuthContext';
import { useAgentChatStore } from './stores/agentChatStore';
import { installEnglishOnlyDomGuard } from './utils/englishOnlyDom';
import './App.css';

const AppContent: React.FC = () => {
  const location = useLocation();
  const { authEnabled, loggedIn, isLoading, loadError, refreshStatus } = useAuth();

  useEffect(() => {
    useAgentChatStore.getState().setCurrentRoute(location.pathname);
  }, [location.pathname]);

  if (isLoading) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-base">
        <div className="h-8 w-8 animate-spin rounded-full border-2 border-cyan/20 border-t-cyan" />
      </div>
    );
  }

  if (loadError) {
    return (
      <div className="flex min-h-screen flex-col items-center justify-center gap-4 bg-base px-4">
        <div className="w-full max-w-lg">
          <ApiErrorAlert error={loadError} />
        </div>
        <button
          type="button"
          className="btn-primary"
          onClick={() => void refreshStatus()}
        >
          Retry
        </button>
      </div>
    );
  }

  if (authEnabled && !loggedIn) {
    if (location.pathname === '/login') {
      return <LoginPage />;
    }
    const redirect = encodeURIComponent(location.pathname + location.search);
    return <Navigate to={`/login?redirect=${redirect}`} replace />;
  }

  if (location.pathname === '/login') {
    return <Navigate to="/" replace />;
  }

  return (
    <Routes>
      <Route element={<Shell />}>
        <Route path="/" element={<TodayPage />} />
        <Route path="/analyze" element={<HomePage />} />
        {/* Muscle-memory redirect: the single-stock page used to be the home page. */}
        <Route path="/home" element={<Navigate to="/analyze" replace />} />
        <Route path="/guide" element={<GuidePage />} />
        <Route path="/chat" element={<ChatPage />} />
        <Route path="/signals" element={<SignalsPage />} />
        <Route path="/data" element={<DataPage />} />
        <Route path="/rotation" element={<RotationPage />} />
        <Route path="/positioning" element={<PositioningPage />} />
        <Route path="/sources" element={<SourcesPage />} />
        <Route path="/portfolio" element={<PortfolioPage />} />
        <Route path="/backtest" element={<BacktestPage />} />
        <Route path="/scorecard" element={<ScorecardPage />} />
        <Route path="/settings" element={<SettingsPage />} />
        <Route path="*" element={<NotFoundPage />} />
      </Route>
      <Route path="/login" element={<LoginPage />} />
    </Routes>
  );
};

const App: React.FC = () => {
  useEffect(() => installEnglishOnlyDomGuard(), []);

  return (
    <Router>
      <AuthProvider>
        <AppContent />
      </AuthProvider>
    </Router>
  );
};

export default App;
