/**
 * API Response Cache with TTL
 *
 * Provides simple TTL-based caching for API responses to reduce
 * excessive HTTP requests during a user session.
 *
 * Usage:
 *   const cached = await apiCache.get('sessions', () => listSessions(...));
 *   apiCache.invalidate('sessions'); // when data changes
 */

interface CacheEntry<T> {
  data: T;
  expiresAt: number;
  promise?: Promise<T>; // For deduplicating in-flight requests
}

interface CacheConfig {
  /** Time-to-live in milliseconds (default: 60000 = 1 minute) */
  ttlMs: number;
  /** Stale-while-revalidate: return stale data while refreshing in background */
  staleWhileRevalidate?: boolean;
}

const DEFAULT_TTL_MS = 60 * 1000; // 1 minute
const SKILLS_TTL_MS = 5 * 60 * 1000; // 5 minutes - skills rarely change

class ApiCache {
  private cache = new Map<string, CacheEntry<unknown>>();
  private configs = new Map<string, CacheConfig>();

  constructor() {
    // Set default TTLs for known cache keys
    this.configs.set('sessions', { ttlMs: DEFAULT_TTL_MS, staleWhileRevalidate: true });
    this.configs.set('skills', { ttlMs: SKILLS_TTL_MS, staleWhileRevalidate: false });
  }

  /**
   * Get cached data or fetch fresh data if cache is expired/missing.
   *
   * @param key - Cache key (e.g., 'sessions', 'skills')
   * @param fetcher - Async function to fetch fresh data
   * @param config - Optional cache configuration override
   * @returns Cached or freshly fetched data
   */
  async get<T>(
    key: string,
    fetcher: () => Promise<T>,
    config?: Partial<CacheConfig>
  ): Promise<T> {
    const now = Date.now();
    const entry = this.cache.get(key) as CacheEntry<T> | undefined;
    const cacheConfig = { ...this.getConfig(key), ...config };

    // Check if we have valid cached data
    if (entry) {
      const isValid = entry.expiresAt > now;
      const isStale = !isValid && cacheConfig.staleWhileRevalidate;

      if (isValid) {
        return entry.data;
      }

      // Return stale data while revalidating in background
      if (isStale && entry.data) {
        this.revalidate(key, fetcher, cacheConfig);
        return entry.data;
      }

      // Deduplicate in-flight requests
      if (entry.promise) {
        return entry.promise;
      }
    }

    // Fetch fresh data
    return this.fetchAndCache(key, fetcher, cacheConfig);
  }

  /**
   * Invalidate a cache entry, forcing next get() to fetch fresh data.
   */
  invalidate(key: string): void {
    this.cache.delete(key);
  }

  /**
   * Invalidate all cache entries.
   */
  invalidateAll(): void {
    this.cache.clear();
  }

  /**
   * Check if a cache entry exists and is valid.
   */
  has(key: string): boolean {
    const entry = this.cache.get(key);
    return entry !== undefined && entry.expiresAt > Date.now();
  }

  /**
   * Get raw cached data without fetching (returns undefined if not cached/expired).
   */
  peek<T>(key: string): T | undefined {
    const entry = this.cache.get(key) as CacheEntry<T> | undefined;
    if (entry && entry.expiresAt > Date.now()) {
      return entry.data;
    }
    return undefined;
  }

  /**
   * Update cache entry directly (useful for optimistic updates).
   */
  set<T>(key: string, data: T, config?: Partial<CacheConfig>): void {
    const cacheConfig = { ...this.getConfig(key), ...config };
    this.cache.set(key, {
      data,
      expiresAt: Date.now() + cacheConfig.ttlMs,
    });
  }

  private getConfig(key: string): CacheConfig {
    return this.configs.get(key) || { ttlMs: DEFAULT_TTL_MS };
  }

  private async fetchAndCache<T>(
    key: string,
    fetcher: () => Promise<T>,
    config: CacheConfig
  ): Promise<T> {
    const promise = fetcher();

    // Store promise for deduplication
    const entry: CacheEntry<T> = {
      data: undefined as T,
      expiresAt: 0,
      promise,
    };
    this.cache.set(key, entry as CacheEntry<unknown>);

    try {
      const data = await promise;
      this.cache.set(key, {
        data,
        expiresAt: Date.now() + config.ttlMs,
      });
      return data;
    } catch (error) {
      // Remove failed entry
      this.cache.delete(key);
      throw error;
    }
  }

  private revalidate<T>(
    key: string,
    fetcher: () => Promise<T>,
    config: CacheConfig
  ): void {
    // Background revalidation - don't await
    fetcher()
      .then((data) => {
        this.cache.set(key, {
          data,
          expiresAt: Date.now() + config.ttlMs,
        });
      })
      .catch((error) => {
        console.warn(`Background revalidation failed for ${key}:`, error);
        // Keep stale data on failure
      });
  }
}

// Export singleton instance
export const apiCache = new ApiCache();

// Export class for testing
export { ApiCache };
