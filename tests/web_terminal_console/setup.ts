/**
 * Vitest setup file for web terminal client tests.
 * Configures jsdom environment and jest-dom matchers.
 */
import '@testing-library/jest-dom/vitest';
import { afterAll, afterEach, beforeAll, vi } from 'vitest';
import { cleanup } from '@testing-library/react';
import { server } from './mocks/server';

// Start MSW server before all tests
beforeAll(() => {
  server.listen({ onUnhandledRequest: 'warn' });
});

// Reset handlers after each test
afterEach(() => {
  server.resetHandlers();
  cleanup();
  vi.clearAllTimers();
  vi.useRealTimers();
});

// Stop MSW server after all tests
afterAll(() => {
  server.close();
});

// Suppress React 18 act() warnings from userEvent and async state updates.
// These are false positives caused by React 18's batching model interacting
// with Testing Library — tests pass correctly and behavior is verified.
const originalConsoleError = console.error;
console.error = (...args: unknown[]) => {
  if (typeof args[0] === 'string' && args[0].includes('was not wrapped in act')) {
    return;
  }
  originalConsoleError(...args);
};

// Mock window.matchMedia
Object.defineProperty(window, 'matchMedia', {
  writable: true,
  value: vi.fn().mockImplementation((query: string) => ({
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

// Mock ResizeObserver
global.ResizeObserver = vi.fn().mockImplementation(() => ({
  observe: vi.fn(),
  unobserve: vi.fn(),
  disconnect: vi.fn(),
}));

// Mock scrollIntoView
Element.prototype.scrollIntoView = vi.fn();

// Mock URL.createObjectURL and URL.revokeObjectURL (not available in jsdom)
let objectUrlCounter = 0;

URL.createObjectURL = vi.fn(() => `blob:test-url-${++objectUrlCounter}`);
URL.revokeObjectURL = vi.fn();

// Mock ClipboardItem (not available in jsdom)
if (typeof globalThis.ClipboardItem === 'undefined') {
  globalThis.ClipboardItem = class ClipboardItem {
    readonly types: string[];
    constructor(private items: Record<string, Blob>) {
      this.types = Object.keys(items);
    }
    getType(type: string): Promise<Blob> {
      const blob = this.items[type];
      return blob ? Promise.resolve(blob) : Promise.reject(new Error(`Type ${type} not found`));
    }
  } as unknown as typeof globalThis.ClipboardItem;
}
