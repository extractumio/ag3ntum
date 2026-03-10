import { render, screen } from '@testing-library/react';
import React from 'react';
import { describe, expect, it } from 'vitest';
import { ConnectionTestResult } from '../../../src/web_terminal_client/src/pages/settings/ConnectionTestResult';
import type { TestSSHConnectionResponse } from '../../../src/web_terminal_client/src/types/ssh';

describe('ConnectionTestResult', () => {
  it('renders nothing when result is null and not loading', () => {
    const { container } = render(<ConnectionTestResult result={null} loading={false} />);
    expect(container.firstChild).toBeNull();
  });

  it('shows loading spinner when loading', () => {
    render(<ConnectionTestResult result={null} loading />);
    expect(screen.getByText('Testing connection...')).toBeInTheDocument();
    expect(screen.getByLabelText('Testing connection')).toBeInTheDocument();
  });

  it('shows success state with latency and fingerprint', () => {
    const result: TestSSHConnectionResponse = {
      status: 'success',
      message: 'Connected successfully',
      latency_ms: 42,
      host_key_fingerprint: 'SHA256:abcdef123456',
      host_key_type: 'ed25519',
      server_banner: 'OpenSSH_8.9',
    };
    render(<ConnectionTestResult result={result} loading={false} />);

    expect(screen.getByText('Connection successful')).toBeInTheDocument();
    expect(screen.getByText('Connected successfully')).toBeInTheDocument();
    expect(screen.getByText('42 ms')).toBeInTheDocument();
    expect(screen.getByText('SHA256:abcdef123456')).toBeInTheDocument();
    expect(screen.getByText('ed25519')).toBeInTheDocument();
    expect(screen.getByText('OpenSSH_8.9')).toBeInTheDocument();
  });

  it('shows failure state with error message', () => {
    const result: TestSSHConnectionResponse = {
      status: 'failed',
      message: 'Connection refused',
      error_code: 'CONN_REFUSED',
    };
    render(<ConnectionTestResult result={result} loading={false} />);

    expect(screen.getByText('Connection failed')).toBeInTheDocument();
    expect(screen.getByText('Connection refused')).toBeInTheDocument();
    expect(screen.getByText(/CONN_REFUSED/)).toBeInTheDocument();
  });

  it('does not show latency section when latency is absent', () => {
    const result: TestSSHConnectionResponse = {
      status: 'success',
      message: 'Connected',
    };
    render(<ConnectionTestResult result={result} loading={false} />);

    expect(screen.queryByText(/ms/)).toBeNull();
  });

  it('does not show error_code when absent', () => {
    const result: TestSSHConnectionResponse = {
      status: 'failed',
      message: 'Auth failed',
    };
    render(<ConnectionTestResult result={result} loading={false} />);

    expect(screen.queryByText(/Error:/)).toBeNull();
  });
});
