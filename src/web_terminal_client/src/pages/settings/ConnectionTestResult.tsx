import type { TestSSHConnectionResponse } from '../../types/ssh';

interface ConnectionTestResultProps {
  result: TestSSHConnectionResponse | null;
  loading: boolean;
}

export function ConnectionTestResult({ result, loading }: ConnectionTestResultProps) {
  if (loading) {
    return (
      <div className="ssh-test-result ssh-test-loading">
        <span className="ssh-test-spinner" aria-label="Testing connection" />
        <span>Testing connection...</span>
      </div>
    );
  }

  if (!result) return null;

  const isSuccess = result.status === 'success';

  return (
    <div className={`ssh-test-result ${isSuccess ? 'ssh-test-success' : 'ssh-test-failure'}`}>
      <div className="ssh-test-status">
        <span className="ssh-test-icon">{isSuccess ? '✓' : '✗'}</span>
        <strong>{isSuccess ? 'Connection successful' : 'Connection failed'}</strong>
      </div>
      <div className="ssh-test-message">{result.message}</div>
      {isSuccess && (
        <div className="ssh-test-details">
          {result.latency_ms !== undefined && (
            <div className="ssh-test-detail">
              <span className="ssh-test-detail-label">Latency</span>
              <span>{result.latency_ms} ms</span>
            </div>
          )}
          {result.host_key_fingerprint && (
            <div className="ssh-test-detail">
              <span className="ssh-test-detail-label">Host key</span>
              <code className="ssh-fingerprint">{result.host_key_fingerprint}</code>
            </div>
          )}
          {result.host_key_type && (
            <div className="ssh-test-detail">
              <span className="ssh-test-detail-label">Key type</span>
              <span>{result.host_key_type}</span>
            </div>
          )}
          {result.server_banner && (
            <div className="ssh-test-detail">
              <span className="ssh-test-detail-label">Banner</span>
              <span className="ssh-banner">{result.server_banner}</span>
            </div>
          )}
        </div>
      )}
      {!isSuccess && result.error_code && (
        <div className="ssh-test-error-code">Error: {result.error_code}</div>
      )}
    </div>
  );
}
