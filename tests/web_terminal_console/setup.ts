import '@testing-library/jest-dom';
import { cleanup } from '@testing-library/react';
import { afterAll, afterEach, beforeAll, vi } from 'vitest';
import { server } from './mocks/server';

// Store original timer functions before any tests fake them
const originalSetInterval = globalThis.setInterval;
const originalClearInterval = globalThis.clearInterval;
const originalSetTimeout = globalThis.setTimeout;
const originalClearTimeout = globalThis.clearTimeout;

// Ensure timer functions are always available globally (for React cleanup)
if (typeof globalThis.clearInterval === 'undefined') {
  globalThis.clearInterval = originalClearInterval;
}
if (typeof globalThis.setInterval === 'undefined') {
  globalThis.setInterval = originalSetInterval;
}
if (typeof globalThis.clearTimeout === 'undefined') {
  globalThis.clearTimeout = originalClearTimeout;
}
if (typeof globalThis.setTimeout === 'undefined') {
  globalThis.setTimeout = originalSetTimeout;
}

// Establish API mocking before all tests
beforeAll(() => {
  server.listen({ onUnhandledRequest: 'error' });
});

// Reset any request handlers after each test
afterEach(() => {
  server.resetHandlers();
  cleanup();
  // Clear localStorage between tests
  localStorage.clear();
});

// Clean up after the tests are finished
afterAll(() => {
  server.close();
});

// Mock localStorage with actual storage functionality
const localStorageStore: Record<string, string> = {};
const localStorageMock = {
  getItem: vi.fn((key: string) => localStorageStore[key] ?? null),
  setItem: vi.fn((key: string, value: string) => {
    localStorageStore[key] = value;
  }),
  removeItem: vi.fn((key: string) => {
    delete localStorageStore[key];
  }),
  clear: vi.fn(() => {
    Object.keys(localStorageStore).forEach((key) => delete localStorageStore[key]);
  }),
  get length() {
    return Object.keys(localStorageStore).length;
  },
  key: vi.fn((index: number) => Object.keys(localStorageStore)[index] ?? null),
};
Object.defineProperty(window, 'localStorage', {
  value: localStorageMock,
  writable: true,
  configurable: true,
});

// Mock ClipboardItem (not available in jsdom)
class ClipboardItemMock {
  private items: Record<string, Blob>;

  constructor(items: Record<string, Blob>) {
    this.items = items;
  }

  getType(type: string): Promise<Blob> {
    return Promise.resolve(this.items[type]);
  }

  get types(): string[] {
    return Object.keys(this.items);
  }
}

(globalThis as unknown as { ClipboardItem: typeof ClipboardItemMock }).ClipboardItem = ClipboardItemMock;

// Mock clipboard API with proper vi.fn() spies
const clipboardWriteText = vi.fn().mockResolvedValue(undefined);
const clipboardWrite = vi.fn().mockResolvedValue(undefined);
const clipboardReadText = vi.fn().mockResolvedValue('');
const clipboardRead = vi.fn().mockResolvedValue([]);

Object.defineProperty(navigator, 'clipboard', {
  value: {
    writeText: clipboardWriteText,
    write: clipboardWrite,
    readText: clipboardReadText,
    read: clipboardRead,
  },
  writable: true,
  configurable: true,
});

// Mock URL.createObjectURL and URL.revokeObjectURL
URL.createObjectURL = vi.fn(() => 'blob:mock-url');
URL.revokeObjectURL = vi.fn();

// Mock ResizeObserver
class ResizeObserverMock {
  observe = vi.fn();
  unobserve = vi.fn();
  disconnect = vi.fn();
}
window.ResizeObserver = ResizeObserverMock;

// Mock matchMedia
Object.defineProperty(window, 'matchMedia', {
  writable: true,
  value: vi.fn().mockImplementation((query) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: vi.fn(),
    removeListener: vi.fn(),
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    dispatchEvent: vi.fn(),
  })),
});

// Mock EventSource for SSE tests
class EventSourceMock {
  static CONNECTING = 0;
  static OPEN = 1;
  static CLOSED = 2;

  readyState = EventSourceMock.CONNECTING;
  url: string;
  onopen: ((event: Event) => void) | null = null;
  onmessage: ((event: MessageEvent) => void) | null = null;
  onerror: ((event: Event) => void) | null = null;

  constructor(url: string) {
    this.url = url;
    // Simulate connection opening
    setTimeout(() => {
      this.readyState = EventSourceMock.OPEN;
      this.onopen?.(new Event('open'));
    }, 0);
  }

  close() {
    this.readyState = EventSourceMock.CLOSED;
  }

  addEventListener = vi.fn();
  removeEventListener = vi.fn();
  dispatchEvent = vi.fn();
}

(global as unknown as { EventSource: typeof EventSourceMock }).EventSource = EventSourceMock;

// Suppress console errors during tests (optional, remove if you want to see them)
// vi.spyOn(console, 'error').mockImplementation(() => {});
// vi.spyOn(console, 'warn').mockImplementation(() => {});
