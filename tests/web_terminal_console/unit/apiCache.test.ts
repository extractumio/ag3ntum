/**
 * Tests for apiCache
 *
 * Tests the API response caching functionality including:
 * - Basic caching with TTL
 * - Stale-while-revalidate behavior
 * - Cache invalidation
 * - Request deduplication
 * - Cache peek and has methods
 * - Direct set method
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ApiCache, apiCache } from '../../../src/web_terminal_client/src/apiCache';

describe('ApiCache', () => {
  let cache: ApiCache;

  beforeEach(() => {
    // Create a fresh cache instance for each test
    cache = new ApiCache();
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  // ==========================================================================
  // Basic Caching
  // ==========================================================================
  describe('Basic Caching', () => {
    it('caches data and returns it on subsequent requests', async () => {
      let fetchCount = 0;
      const fetcher = vi.fn(async () => {
        fetchCount++;
        return { data: 'value' };
      });

      // First call - should fetch
      const result1 = await cache.get('test-key', fetcher);
      expect(result1).toEqual({ data: 'value' });
      expect(fetcher).toHaveBeenCalledTimes(1);

      // Second call - should return cached
      const result2 = await cache.get('test-key', fetcher);
      expect(result2).toEqual({ data: 'value' });
      expect(fetcher).toHaveBeenCalledTimes(1); // Not called again
    });

    it('respects default TTL of 1 minute', async () => {
      const fetcher = vi.fn(async () => ({ data: 'fresh' }));

      await cache.get('test-key', fetcher);
      expect(fetcher).toHaveBeenCalledTimes(1);

      // Advance time by 30 seconds - still valid
      vi.advanceTimersByTime(30000);
      await cache.get('test-key', fetcher);
      expect(fetcher).toHaveBeenCalledTimes(1);

      // Advance time by another 31 seconds (total 61 seconds) - expired
      vi.advanceTimersByTime(31000);
      await cache.get('test-key', fetcher);
      expect(fetcher).toHaveBeenCalledTimes(2);
    });

    it('uses custom TTL when provided', async () => {
      const fetcher = vi.fn(async () => 'data');

      await cache.get('test-key', fetcher, { ttlMs: 5000 });
      expect(fetcher).toHaveBeenCalledTimes(1);

      // Advance time by 4 seconds - still valid
      vi.advanceTimersByTime(4000);
      await cache.get('test-key', fetcher, { ttlMs: 5000 });
      expect(fetcher).toHaveBeenCalledTimes(1);

      // Advance time by 2 more seconds (total 6 seconds) - expired
      vi.advanceTimersByTime(2000);
      await cache.get('test-key', fetcher, { ttlMs: 5000 });
      expect(fetcher).toHaveBeenCalledTimes(2);
    });

    it('uses longer TTL for skills cache key', async () => {
      const fetcher = vi.fn(async () => ['skill1', 'skill2']);

      await cache.get('skills', fetcher);
      expect(fetcher).toHaveBeenCalledTimes(1);

      // Advance time by 4 minutes - still valid (skills TTL is 5 minutes)
      vi.advanceTimersByTime(4 * 60 * 1000);
      await cache.get('skills', fetcher);
      expect(fetcher).toHaveBeenCalledTimes(1);

      // Advance time by 2 more minutes (total 6 minutes) - expired
      vi.advanceTimersByTime(2 * 60 * 1000);
      await cache.get('skills', fetcher);
      expect(fetcher).toHaveBeenCalledTimes(2);
    });
  });

  // ==========================================================================
  // Stale-While-Revalidate
  // ==========================================================================
  describe('Stale-While-Revalidate', () => {
    it('returns stale data while revalidating in background', async () => {
      const fetcher = vi.fn()
        .mockResolvedValueOnce('first')
        .mockResolvedValueOnce('second');

      // First fetch
      const result1 = await cache.get('sessions', fetcher);
      expect(result1).toBe('first');

      // Expire the cache
      vi.advanceTimersByTime(61000);

      // Should return stale data immediately
      const result2 = await cache.get('sessions', fetcher);
      expect(result2).toBe('first'); // Returns stale data

      // Background revalidation should have started
      // Wait for the background fetch to complete
      await vi.runAllTimersAsync();

      // Now should have fresh data
      const result3 = await cache.get('sessions', fetcher);
      expect(result3).toBe('second');
    });

    it('keeps stale data if background revalidation fails', async () => {
      const consoleWarnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {});

      // Create a controlled fetcher for precise control
      let fetcherCallCount = 0;
      const fetcher = vi.fn(() => {
        fetcherCallCount++;
        if (fetcherCallCount === 1) {
          return Promise.resolve('original');
        } else if (fetcherCallCount === 2) {
          return Promise.reject(new Error('Network error'));
        }
        return Promise.resolve('fresh');
      });

      // First fetch - gets 'original'
      const result1 = await cache.get('sessions', fetcher);
      expect(result1).toBe('original');
      expect(fetcherCallCount).toBe(1);

      // Expire the cache
      vi.advanceTimersByTime(61000);

      // Second fetch - should return stale 'original' immediately
      // and trigger background revalidation
      const result2 = await cache.get('sessions', fetcher);
      expect(result2).toBe('original');
      // Fetcher might be called again for background revalidation
      expect(fetcherCallCount).toBeGreaterThanOrEqual(1);

      // Wait for background revalidation to complete (and fail)
      await vi.runAllTimersAsync();

      // Warning should have been logged for background revalidation failure
      expect(consoleWarnSpy).toHaveBeenCalledWith(
        expect.stringContaining('Background revalidation failed'),
        expect.any(Error)
      );

      consoleWarnSpy.mockRestore();
    });
  });

  // ==========================================================================
  // Request Deduplication
  // ==========================================================================
  describe('Request Deduplication', () => {
    it('deduplicates concurrent requests for the same key', async () => {
      let resolvePromise: (value: string) => void;
      const slowFetcher = vi.fn(() => new Promise<string>((resolve) => {
        resolvePromise = resolve;
      }));

      // Start two concurrent requests
      const promise1 = cache.get('test-key', slowFetcher);
      const promise2 = cache.get('test-key', slowFetcher);

      // Should only call fetcher once
      expect(slowFetcher).toHaveBeenCalledTimes(1);

      // Resolve the promise
      resolvePromise!('data');

      // Both promises should resolve to the same value
      const [result1, result2] = await Promise.all([promise1, promise2]);
      expect(result1).toBe('data');
      expect(result2).toBe('data');
    });
  });

  // ==========================================================================
  // Cache Invalidation
  // ==========================================================================
  describe('Cache Invalidation', () => {
    it('invalidates a specific cache entry', async () => {
      const fetcher = vi.fn(async () => 'data');

      await cache.get('test-key', fetcher);
      expect(fetcher).toHaveBeenCalledTimes(1);

      // Invalidate the cache
      cache.invalidate('test-key');

      // Should fetch again
      await cache.get('test-key', fetcher);
      expect(fetcher).toHaveBeenCalledTimes(2);
    });

    it('invalidates all cache entries', async () => {
      const fetcher1 = vi.fn(async () => 'data1');
      const fetcher2 = vi.fn(async () => 'data2');

      await cache.get('key1', fetcher1);
      await cache.get('key2', fetcher2);
      expect(fetcher1).toHaveBeenCalledTimes(1);
      expect(fetcher2).toHaveBeenCalledTimes(1);

      // Invalidate all
      cache.invalidateAll();

      // Should fetch again
      await cache.get('key1', fetcher1);
      await cache.get('key2', fetcher2);
      expect(fetcher1).toHaveBeenCalledTimes(2);
      expect(fetcher2).toHaveBeenCalledTimes(2);
    });
  });

  // ==========================================================================
  // has() Method
  // ==========================================================================
  describe('has() Method', () => {
    it('returns true for valid cached entries', async () => {
      await cache.get('test-key', async () => 'data');
      expect(cache.has('test-key')).toBe(true);
    });

    it('returns false for non-existent entries', () => {
      expect(cache.has('non-existent')).toBe(false);
    });

    it('returns false for expired entries', async () => {
      await cache.get('test-key', async () => 'data', { ttlMs: 1000 });
      expect(cache.has('test-key')).toBe(true);

      // Expire the cache
      vi.advanceTimersByTime(2000);
      expect(cache.has('test-key')).toBe(false);
    });
  });

  // ==========================================================================
  // peek() Method
  // ==========================================================================
  describe('peek() Method', () => {
    it('returns cached data without triggering fetch', async () => {
      await cache.get('test-key', async () => 'cached-data');
      const result = cache.peek<string>('test-key');
      expect(result).toBe('cached-data');
    });

    it('returns undefined for non-existent entries', () => {
      const result = cache.peek('non-existent');
      expect(result).toBeUndefined();
    });

    it('returns undefined for expired entries', async () => {
      await cache.get('test-key', async () => 'data', { ttlMs: 1000 });

      // Expire the cache
      vi.advanceTimersByTime(2000);
      const result = cache.peek('test-key');
      expect(result).toBeUndefined();
    });
  });

  // ==========================================================================
  // set() Method
  // ==========================================================================
  describe('set() Method', () => {
    it('directly sets cache data', () => {
      cache.set('test-key', 'direct-data');
      expect(cache.peek('test-key')).toBe('direct-data');
    });

    it('uses custom TTL when setting', () => {
      cache.set('test-key', 'data', { ttlMs: 5000 });
      expect(cache.has('test-key')).toBe(true);

      vi.advanceTimersByTime(6000);
      expect(cache.has('test-key')).toBe(false);
    });

    it('overwrites existing cache entries', async () => {
      await cache.get('test-key', async () => 'original');
      cache.set('test-key', 'updated');
      expect(cache.peek('test-key')).toBe('updated');
    });
  });

  // ==========================================================================
  // Error Handling
  // ==========================================================================
  describe('Error Handling', () => {
    it('removes cache entry when fetch fails', async () => {
      const fetcher = vi.fn().mockRejectedValue(new Error('Fetch failed'));

      await expect(cache.get('test-key', fetcher)).rejects.toThrow('Fetch failed');
      expect(cache.has('test-key')).toBe(false);
    });

    it('allows retry after fetch failure', async () => {
      const fetcher = vi.fn()
        .mockRejectedValueOnce(new Error('First failure'))
        .mockResolvedValueOnce('success');

      await expect(cache.get('test-key', fetcher)).rejects.toThrow('First failure');

      const result = await cache.get('test-key', fetcher);
      expect(result).toBe('success');
    });
  });

  // ==========================================================================
  // Singleton Instance
  // ==========================================================================
  describe('Singleton Instance', () => {
    it('exports a singleton apiCache instance', () => {
      expect(apiCache).toBeInstanceOf(ApiCache);
    });

    it('singleton has expected default configs', async () => {
      // The singleton should have sessions with stale-while-revalidate
      // and skills with longer TTL
      const sessionsFetcher = vi.fn(async () => []);
      const skillsFetcher = vi.fn(async () => []);

      await apiCache.get('sessions', sessionsFetcher);
      await apiCache.get('skills', skillsFetcher);

      // Clear for next test
      apiCache.invalidateAll();
    });
  });
});
