import { render, RenderOptions } from '@testing-library/react';
import React, { ReactElement } from 'react';
import { BrowserRouter, MemoryRouter } from 'react-router-dom';
import { AuthProvider } from '../../../src/web_terminal_client/src/AuthContext';

interface CustomRenderOptions extends Omit<RenderOptions, 'wrapper'> {
  initialEntries?: string[];
  useMemoryRouter?: boolean;
}

/**
 * Custom render function that wraps components with all necessary providers.
 */
export function renderWithProviders(
  ui: ReactElement,
  options: CustomRenderOptions = {}
) {
  const { initialEntries = ['/'], useMemoryRouter = false, ...renderOptions } = options;

  function Wrapper({ children }: { children: React.ReactNode }) {
    const Router = useMemoryRouter
      ? ({ children }: { children: React.ReactNode }) => (
          <MemoryRouter initialEntries={initialEntries}>{children}</MemoryRouter>
        )
      : BrowserRouter;

    return (
      <Router>
        <AuthProvider>{children}</AuthProvider>
      </Router>
    );
  }

  return {
    ...render(ui, { wrapper: Wrapper, ...renderOptions }),
  };
}

/**
 * Render with just a router (no AuthProvider) for simpler component tests.
 */
export function renderWithRouter(
  ui: ReactElement,
  options: CustomRenderOptions = {}
) {
  const { initialEntries = ['/'], useMemoryRouter = true, ...renderOptions } = options;

  function Wrapper({ children }: { children: React.ReactNode }) {
    return useMemoryRouter ? (
      <MemoryRouter initialEntries={initialEntries}>{children}</MemoryRouter>
    ) : (
      <BrowserRouter>{children}</BrowserRouter>
    );
  }

  return {
    ...render(ui, { wrapper: Wrapper, ...renderOptions }),
  };
}

/**
 * Wait for async operations to complete.
 */
export function waitForAsync(ms: number = 0): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/**
 * Create a deferred promise for testing async flows.
 */
export function createDeferred<T>() {
  let resolve: (value: T) => void;
  let reject: (reason?: unknown) => void;

  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });

  return { promise, resolve: resolve!, reject: reject! };
}
