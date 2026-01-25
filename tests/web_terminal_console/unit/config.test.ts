/**
 * Tests for config.ts
 *
 * Tests the configuration loading functionality including:
 * - Default configuration values
 * - YAML config loading
 * - API URL resolution with browser hostname
 * - Error handling for missing/invalid config
 * - Caching of loaded config
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { loadConfig } from '../../../src/web_terminal_client/src/config';

describe('Config', () => {
  const originalFetch = globalThis.fetch;
  const originalWindow = globalThis.window;

  beforeEach(() => {
    // Reset the cached promise before each test
    loadConfig.cachedPromise = null;

    // Mock window.location
    Object.defineProperty(globalThis, 'window', {
      value: {
        location: {
          protocol: 'http:',
          hostname: 'localhost',
        },
      },
      writable: true,
      configurable: true,
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
    loadConfig.cachedPromise = null;
    globalThis.fetch = originalFetch;
    if (originalWindow) {
      Object.defineProperty(globalThis, 'window', {
        value: originalWindow,
        writable: true,
        configurable: true,
      });
    }
  });

  // ==========================================================================
  // Default Configuration
  // ==========================================================================
  describe('Default Configuration', () => {
    it('returns default config when fetch fails', async () => {
      globalThis.fetch = vi.fn().mockRejectedValue(new Error('Network error'));

      const config = await loadConfig();

      expect(config.api.base_url).toMatch(/http:\/\/localhost:\d+/);
      expect(config.ui.max_output_lines).toBe(1000);
      expect(config.ui.auto_scroll).toBe(true);
    });

    it('returns default config when response is not ok', async () => {
      globalThis.fetch = vi.fn().mockResolvedValue({
        ok: false,
        status: 404,
      });

      const config = await loadConfig();

      expect(config.api.base_url).toMatch(/http:\/\/localhost:\d+/);
      expect(config.ui.max_output_lines).toBe(1000);
      expect(config.ui.auto_scroll).toBe(true);
    });
  });

  // ==========================================================================
  // YAML Config Loading
  // ==========================================================================
  describe('YAML Config Loading', () => {
    it('loads and parses YAML config', async () => {
      const yamlContent = `
api:
  base_url: http://example.com:8080
ui:
  max_output_lines: 500
  auto_scroll: false
`;
      globalThis.fetch = vi.fn().mockResolvedValue({
        ok: true,
        text: () => Promise.resolve(yamlContent),
      });

      const config = await loadConfig();

      // URL hostname should be replaced with browser hostname
      expect(config.api.base_url).toContain('localhost');
      expect(config.api.base_url).toContain(':8080');
      expect(config.ui.max_output_lines).toBe(500);
      expect(config.ui.auto_scroll).toBe(false);
    });

    it('uses default UI values when not specified in config', async () => {
      const yamlContent = `
api:
  base_url: http://example.com:9000
`;
      globalThis.fetch = vi.fn().mockResolvedValue({
        ok: true,
        text: () => Promise.resolve(yamlContent),
      });

      const config = await loadConfig();

      expect(config.ui.max_output_lines).toBe(1000);
      expect(config.ui.auto_scroll).toBe(true);
    });

    it('handles empty YAML config', async () => {
      globalThis.fetch = vi.fn().mockResolvedValue({
        ok: true,
        text: () => Promise.resolve(''),
      });

      const config = await loadConfig();

      expect(config.api.base_url).toMatch(/http:\/\/localhost:\d+/);
      expect(config.ui.max_output_lines).toBe(1000);
    });

    it('handles invalid YAML gracefully', async () => {
      const consoleWarnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {});

      globalThis.fetch = vi.fn().mockResolvedValue({
        ok: true,
        text: () => Promise.resolve('{ invalid yaml ['),
      });

      const config = await loadConfig();

      // Should fall back to defaults
      expect(config.api.base_url).toMatch(/http:\/\/localhost:\d+/);
      expect(consoleWarnSpy).toHaveBeenCalled();

      consoleWarnSpy.mockRestore();
    });
  });

  // ==========================================================================
  // API URL Resolution
  // ==========================================================================
  describe('API URL Resolution', () => {
    it('preserves protocol from config', async () => {
      const yamlContent = `
api:
  base_url: https://example.com:443
`;
      globalThis.fetch = vi.fn().mockResolvedValue({
        ok: true,
        text: () => Promise.resolve(yamlContent),
      });

      const config = await loadConfig();

      expect(config.api.base_url).toMatch(/^https:\/\//);
    });

    it('preserves port from config', async () => {
      const yamlContent = `
api:
  base_url: http://example.com:9999
`;
      globalThis.fetch = vi.fn().mockResolvedValue({
        ok: true,
        text: () => Promise.resolve(yamlContent),
      });

      const config = await loadConfig();

      expect(config.api.base_url).toContain(':9999');
    });

    it('uses browser hostname for API URL', async () => {
      // Set browser hostname
      (globalThis.window as { location: { hostname: string; protocol: string } }).location.hostname = 'myserver.local';

      const yamlContent = `
api:
  base_url: http://192.168.1.100:40080
`;
      globalThis.fetch = vi.fn().mockResolvedValue({
        ok: true,
        text: () => Promise.resolve(yamlContent),
      });

      const config = await loadConfig();

      expect(config.api.base_url).toContain('myserver.local');
      expect(config.api.base_url).not.toContain('192.168.1.100');
    });

    it('removes trailing slash from API URL', async () => {
      const yamlContent = `
api:
  base_url: http://example.com:8080/
`;
      globalThis.fetch = vi.fn().mockResolvedValue({
        ok: true,
        text: () => Promise.resolve(yamlContent),
      });

      const config = await loadConfig();

      expect(config.api.base_url).not.toMatch(/\/$/);
    });

    it('handles invalid URL in config gracefully', async () => {
      const consoleWarnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {});

      const yamlContent = `
api:
  base_url: not-a-valid-url
`;
      globalThis.fetch = vi.fn().mockResolvedValue({
        ok: true,
        text: () => Promise.resolve(yamlContent),
      });

      const config = await loadConfig();

      // Should fall back to default URL
      expect(config.api.base_url).toMatch(/http:\/\/localhost:\d+/);
      expect(consoleWarnSpy).toHaveBeenCalled();

      consoleWarnSpy.mockRestore();
    });
  });

  // ==========================================================================
  // Caching
  // ==========================================================================
  describe('Caching', () => {
    it('caches config and returns same promise on subsequent calls', async () => {
      let fetchCount = 0;
      globalThis.fetch = vi.fn(async () => {
        fetchCount++;
        return {
          ok: true,
          text: () => Promise.resolve('api:\n  base_url: http://test.com:8080'),
        };
      });

      const config1 = await loadConfig();
      const config2 = await loadConfig();

      expect(fetchCount).toBe(1); // Only fetched once
      expect(config1).toBe(config2); // Same object reference
    });

    it('returns cached promise even during loading', async () => {
      let resolvePromise: (value: Response) => void;
      globalThis.fetch = vi.fn(() => new Promise<Response>((resolve) => {
        resolvePromise = resolve;
      }));

      const promise1 = loadConfig();
      const promise2 = loadConfig();

      // Resolve the fetch
      resolvePromise!({
        ok: true,
        text: () => Promise.resolve('api:\n  base_url: http://test.com:8080'),
      } as Response);

      const [config1, config2] = await Promise.all([promise1, promise2]);
      expect(config1).toBe(config2);
    });
  });

  // ==========================================================================
  // Server-Side Rendering (no window)
  // ==========================================================================
  describe('Server-Side Rendering', () => {
    it('uses localhost when window is undefined', async () => {
      // Remove window
      Object.defineProperty(globalThis, 'window', {
        value: undefined,
        writable: true,
        configurable: true,
      });

      // Reset cached promise
      loadConfig.cachedPromise = null;

      globalThis.fetch = vi.fn().mockRejectedValue(new Error('No fetch in SSR'));

      const config = await loadConfig();

      expect(config.api.base_url).toContain('localhost');
    });
  });
});
