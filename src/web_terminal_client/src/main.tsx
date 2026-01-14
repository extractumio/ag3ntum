import React from 'react';
import { createRoot } from 'react-dom/client';
import { BrowserRouter, Routes, Route, Navigate, useParams, useLocation } from 'react-router-dom';
import App from './App';
import './styles.css';

// Session ID validation: must match backend pattern YYYYMMDD_HHMMSS_8hexchars
// This prevents XSS, path traversal, and injection attacks via malformed URLs
const SESSION_ID_PATTERN = /^\d{8}_\d{6}_[a-f0-9]{8}$/;

function isValidSessionId(sessionId: string | undefined): sessionId is string {
  if (!sessionId) return false;
  if (sessionId.length > 24) return false; // Max length: 8 + 1 + 6 + 1 + 8 = 24
  return SESSION_ID_PATTERN.test(sessionId);
}

function TrailingSlashRedirect(): JSX.Element | null {
  const location = useLocation();
  if (!location.pathname.endsWith('/')) {
    return <Navigate to={`${location.pathname}/${location.search}`} replace />;
  }
  return null;
}

function SessionRoute(): JSX.Element {
  const { sessionId } = useParams<{ sessionId: string }>();

  // Validate session ID format before passing to App
  // Invalid IDs redirect to home to prevent injection attacks
  if (!isValidSessionId(sessionId)) {
    return <Navigate to="/" replace />;
  }

  return <App initialSessionId={sessionId} />;
}

const container = document.getElementById('root');

if (!container) {
  throw new Error('Root container not found');
}

createRoot(container).render(
  <React.StrictMode>
    <BrowserRouter>
      <TrailingSlashRedirect />
      <Routes>
        <Route path="/" element={<App />} />
        <Route path="/session/:sessionId/" element={<SessionRoute />} />
        <Route path="/session/:sessionId" element={<Navigate to={window.location.pathname + '/'} replace />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </BrowserRouter>
  </React.StrictMode>
);
