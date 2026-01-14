import YAML from 'yaml';

import type { AppConfig } from './types';

const DEFAULT_CONFIG: AppConfig = {
  api: {
    base_url: 'http://localhost:40080',
  },
  ui: {
    max_output_lines: 1000,
    auto_scroll: true,
  },
};

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
            base_url: parsed?.api?.base_url ?? DEFAULT_CONFIG.api.base_url,
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
