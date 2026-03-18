import { useCallback, useEffect, useState } from 'react';
import { useAuth } from '../../AuthContext';
import { getMyFeatures } from '../../sshApi';
import { SSHProfileList } from './SSHProfileList';

export function SSHSettingsPage() {
  const { token, baseUrl } = useAuth();
  const [sshEnabled, setSshEnabled] = useState<boolean | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

  const checkFeatures = useCallback(() => {
    if (!token || !baseUrl) return;
    getMyFeatures(baseUrl, token)
      .then((features) => {
        setSshEnabled(Boolean(features.ssh_enabled));
      })
      .catch((e) => {
        setLoadError(e instanceof Error ? e.message : String(e));
        setSshEnabled(false);
      });
  }, [token, baseUrl]);

  useEffect(() => { checkFeatures(); }, [checkFeatures]);

  if (sshEnabled === null && !loadError) {
    return <div className="dash-loading">Loading...</div>;
  }

  if (loadError) {
    return <div className="dash-error">{loadError}</div>;
  }

  if (!sshEnabled) {
    return (
      <div>
        <div className="dash-page-header">
          <h2 className="dash-page-title">SSH Connections</h2>
        </div>
        <div className="dash-section" style={{ textAlign: 'center', padding: 'var(--spacing-4xl, 3rem) var(--spacing-xl, 1.5rem)' }}>
          <div style={{ fontSize: '2rem', marginBottom: 'var(--spacing-md, 0.75rem)' }}>&#9888;</div>
          <h3 style={{ marginBottom: 'var(--spacing-sm, 0.5rem)' }}>SSH Connections Not Enabled</h3>
          <p style={{ opacity: 0.6, maxWidth: 480, margin: '0 auto' }}>
            SSH connections are not enabled for your account.
            Contact your administrator to enable this feature.
          </p>
        </div>
      </div>
    );
  }

  return <SSHProfileList />;
}
