import YAML from 'yaml';

import type { AppConfig } from './types';

// Default API port - must match api.external_port in api.yaml
const DEFAULT_API_PORT = 40080;

// Derive API URL from current page hostname
// This ensures the frontend connects to the API on the same host
function getDefaultApiUrl(): string {
  if (typeof window !== 'undefined') {
    const protocol = window.location.protocol;
    const hostname = window.location.hostname;
    return `${protocol}//${hostname}:${DEFAULT_API_PORT}`;
  }
  return `http://localhost:${DEFAULT_API_PORT}`;
}

const DEFAULT_CONFIG: AppConfig = {
  api: {
    base_url: getDefaultApiUrl(),
  },
  ui: {
    max_output_lines: 1000,
    auto_scroll: true,
  },
};

/**
 * Resolve API URL from configuration.
 *
 * Strategy:
 * 1. Extract protocol and port from config URL
 * 2. Always use browser's current hostname for API host
 *
 * This ensures the frontend connects to the API on the same host
 * it was loaded from, regardless of how config was set up.
 * The config's protocol and port are preserved.
 *
 * Examples:
 * - Config: "https://app.example.com:40080", Browser: app.example.com
 *   Result: "https://app.example.com:40080"
 *
 * - Config: "http://192.168.1.100:40080", Browser: localhost
 *   Result: "http://localhost:40080"
 */
function resolveApiUrl(configUrl: string | undefined): string {
  if (typeof window === 'undefined') {
    return configUrl || DEFAULT_CONFIG.api.base_url;
  }

  if (!configUrl) {
    return DEFAULT_CONFIG.api.base_url;
  }

  try {
    const url = new URL(configUrl);
    const browserHostname = window.location.hostname;

    // Use browser hostname, but keep config's protocol and port
    url.hostname = browserHostname;

    return url.toString().replace(/\/$/, ''); // Remove trailing slash
  } catch {
    // If URL parsing fails, fall back to default
    console.warn('Failed to parse config API URL, using default:', configUrl);
    return DEFAULT_CONFIG.api.base_url;
  }
}

export async function loadConfig(): Promise<AppConfig> {
  if (!loadConfig.cachedPromise) {
    loadConfig.cachedPromise = (async () => {
      try {
        const response = await fetch('/config.yaml');
        if (!response.ok) {
          return DEFAULT_CONFIG;
        }

        const text = await response.text();
        const parsed = YAML.parse(text) as Partial<AppConfig> | null;

        return {
          api: {
            base_url: resolveApiUrl(parsed?.api?.base_url),
          },
          ui: {
            max_output_lines: parsed?.ui?.max_output_lines ?? DEFAULT_CONFIG.ui.max_output_lines,
            auto_scroll: parsed?.ui?.auto_scroll ?? DEFAULT_CONFIG.ui.auto_scroll,
          },
        };
      } catch (error) {
        console.warn('Failed to load config.yaml, using defaults.', error);
        return DEFAULT_CONFIG;
      }
    })();
  }

  return loadConfig.cachedPromise;
}

loadConfig.cachedPromise = null as Promise<AppConfig> | null;
