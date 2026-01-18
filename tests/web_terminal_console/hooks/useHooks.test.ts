import { act, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { useEffect, useState } from 'react';

// Store original timer functions
const originalClearInterval = globalThis.clearInterval;
const originalSetInterval = globalThis.setInterval;

const SPINNER_FRAMES = ['⠋', '⠙', '⠹', '⠸', '⠼', '⠴', '⠦', '⠧', '⠇', '⠏'];

function useSpinnerFrame(intervalMs: number = 80): number {
  const [frame, setFrame] = useState(0);

  useEffect(() => {
    const interval = setInterval(() => {
      setFrame((prev) => (prev + 1) % SPINNER_FRAMES.length);
    }, intervalMs);
    return () => clearInterval(interval);
  }, [intervalMs]);

  return frame;
}

function useElapsedTime(startTime: string | null, isRunning: boolean): string {
  const [elapsed, setElapsed] = useState('');

  useEffect(() => {
    if (!isRunning || !startTime) {
      setElapsed('');
      return;
    }

    const updateElapsed = () => {
      const start = new Date(startTime).getTime();
      const now = Date.now();
      const diffMs = now - start;

      const seconds = Math.floor(diffMs / 1000) % 60;
      const minutes = Math.floor(diffMs / 60000) % 60;
      const hours = Math.floor(diffMs / 3600000);

      if (hours > 0) {
        setElapsed(`${hours}h ${minutes}m ${seconds}s`);
      } else if (minutes > 0) {
        setElapsed(`${minutes}m ${seconds}s`);
      } else {
        setElapsed(`${seconds}s`);
      }
    };

    updateElapsed();
    const interval = setInterval(updateElapsed, 1000);
    return () => clearInterval(interval);
  }, [startTime, isRunning]);

  return elapsed;
}

// =============================================================================
// Tests
// =============================================================================

describe('useSpinnerFrame', () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    // Restore real timers before React cleanup runs
    vi.useRealTimers();
    // Ensure clearInterval is available for React cleanup
    globalThis.clearInterval = originalClearInterval;
    globalThis.setInterval = originalSetInterval;
  });

  it('starts at frame 0', () => {
    const { result } = renderHook(() => useSpinnerFrame());

    expect(result.current).toBe(0);
  });

  it('increments frame on interval', () => {
    const { result } = renderHook(() => useSpinnerFrame(100));

    expect(result.current).toBe(0);

    act(() => {
      vi.advanceTimersByTime(100);
    });
    expect(result.current).toBe(1);

    act(() => {
      vi.advanceTimersByTime(100);
    });
    expect(result.current).toBe(2);
  });

  it('wraps around after last frame', () => {
    const { result } = renderHook(() => useSpinnerFrame(100));

    // Advance through all frames
    act(() => {
      vi.advanceTimersByTime(100 * SPINNER_FRAMES.length);
    });

    expect(result.current).toBe(0); // Wrapped back to start
  });

  it('uses default interval of 80ms', () => {
    const { result } = renderHook(() => useSpinnerFrame());

    expect(result.current).toBe(0);

    act(() => {
      vi.advanceTimersByTime(80);
    });
    expect(result.current).toBe(1);
  });

  it('respects custom interval', () => {
    const { result } = renderHook(() => useSpinnerFrame(200));

    act(() => {
      vi.advanceTimersByTime(100);
    });
    expect(result.current).toBe(0); // Not yet

    act(() => {
      vi.advanceTimersByTime(100);
    });
    expect(result.current).toBe(1); // Now
  });

  it('cleans up interval on unmount', () => {
    const clearIntervalSpy = vi.spyOn(global, 'clearInterval');
    const { unmount } = renderHook(() => useSpinnerFrame());

    unmount();

    expect(clearIntervalSpy).toHaveBeenCalled();
  });

  it('resets interval when intervalMs changes', () => {
    const { result, rerender } = renderHook(
      ({ interval }) => useSpinnerFrame(interval),
      { initialProps: { interval: 100 } }
    );

    act(() => {
      vi.advanceTimersByTime(100);
    });
    expect(result.current).toBe(1);

    // Change interval
    rerender({ interval: 200 });

    // Old interval should be cleared, new one should apply
    act(() => {
      vi.advanceTimersByTime(100);
    });
    expect(result.current).toBe(1); // No change yet with new interval

    act(() => {
      vi.advanceTimersByTime(100);
    });
    expect(result.current).toBe(2); // Now changed
  });
});

describe('useElapsedTime', () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    // Restore real timers before React cleanup runs
    vi.useRealTimers();
    // Ensure clearInterval is available for React cleanup
    globalThis.clearInterval = originalClearInterval;
    globalThis.setInterval = originalSetInterval;
  });

  it('returns empty string when not running', () => {
    const { result } = renderHook(() => useElapsedTime('2024-01-15T12:00:00Z', false));

    expect(result.current).toBe('');
  });

  it('returns empty string when startTime is null', () => {
    const { result } = renderHook(() => useElapsedTime(null, true));

    expect(result.current).toBe('');
  });

  it('displays elapsed seconds', () => {
    const now = new Date();
    vi.setSystemTime(now);

    const { result } = renderHook(() => useElapsedTime(now.toISOString(), true));

    expect(result.current).toBe('0s');

    act(() => {
      vi.advanceTimersByTime(5000);
    });

    expect(result.current).toBe('5s');
  });

  it('displays elapsed minutes and seconds', () => {
    const now = new Date();
    vi.setSystemTime(now);

    const { result } = renderHook(() => useElapsedTime(now.toISOString(), true));

    act(() => {
      vi.advanceTimersByTime(125000); // 2 minutes 5 seconds
    });

    expect(result.current).toBe('2m 5s');
  });

  it('displays elapsed hours, minutes, and seconds', () => {
    const now = new Date();
    vi.setSystemTime(now);

    const { result } = renderHook(() => useElapsedTime(now.toISOString(), true));

    act(() => {
      vi.advanceTimersByTime(3725000); // 1 hour 2 minutes 5 seconds
    });

    expect(result.current).toBe('1h 2m 5s');
  });

  it('updates every second', () => {
    const now = new Date();
    vi.setSystemTime(now);

    const { result } = renderHook(() => useElapsedTime(now.toISOString(), true));

    expect(result.current).toBe('0s');

    act(() => {
      vi.advanceTimersByTime(1000);
    });
    expect(result.current).toBe('1s');

    act(() => {
      vi.advanceTimersByTime(1000);
    });
    expect(result.current).toBe('2s');

    act(() => {
      vi.advanceTimersByTime(1000);
    });
    expect(result.current).toBe('3s');
  });

  it('clears elapsed when running becomes false', () => {
    const now = new Date();
    vi.setSystemTime(now);

    const { result, rerender } = renderHook(
      ({ startTime, isRunning }) => useElapsedTime(startTime, isRunning),
      { initialProps: { startTime: now.toISOString(), isRunning: true } }
    );

    act(() => {
      vi.advanceTimersByTime(5000);
    });
    expect(result.current).toBe('5s');

    rerender({ startTime: now.toISOString(), isRunning: false });

    expect(result.current).toBe('');
  });

  it('restarts timing when startTime changes', () => {
    const now = new Date();
    vi.setSystemTime(now);

    const { result, rerender } = renderHook(
      ({ startTime, isRunning }) => useElapsedTime(startTime, isRunning),
      { initialProps: { startTime: now.toISOString(), isRunning: true } }
    );

    act(() => {
      vi.advanceTimersByTime(10000);
    });
    expect(result.current).toBe('10s');

    // New start time (now + 10s becomes the new start)
    const newStartTime = new Date(Date.now()).toISOString();
    rerender({ startTime: newStartTime, isRunning: true });

    // Should reset to near 0
    expect(result.current).toBe('0s');
  });

  it('cleans up interval on unmount', () => {
    const clearIntervalSpy = vi.spyOn(global, 'clearInterval');
    const now = new Date();
    vi.setSystemTime(now);

    const { unmount } = renderHook(() => useElapsedTime(now.toISOString(), true));

    unmount();

    expect(clearIntervalSpy).toHaveBeenCalled();
  });

  it('handles past start time correctly', () => {
    const now = new Date();
    vi.setSystemTime(now);

    // Start time 30 seconds in the past
    const pastTime = new Date(now.getTime() - 30000).toISOString();

    const { result } = renderHook(() => useElapsedTime(pastTime, true));

    expect(result.current).toBe('30s');
  });
});
