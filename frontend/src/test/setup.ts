import '@testing-library/jest-dom';
import { vi } from 'vitest';

// Stub window.matchMedia (jsdom does not implement it)
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

// Stub ResizeObserver (used by Recharts)
globalThis.ResizeObserver = vi.fn().mockImplementation(() => ({
  observe: vi.fn(),
  unobserve: vi.fn(),
  disconnect: vi.fn(),
}));

// Default fetch mock — returns empty structures; individual tests can override
globalThis.fetch = vi.fn().mockResolvedValue({
  ok: true,
  json: async () => ({}),
} as Response);
