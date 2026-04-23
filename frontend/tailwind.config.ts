import type { Config } from 'tailwindcss';

export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        'bg-0': 'var(--bg-0)',
        'bg-1': 'var(--bg-1)',
        'bg-2': 'var(--bg-2)',
        'bg-3': 'var(--bg-3)',
        'bg-4': 'var(--bg-4)',
        'ink-1': 'var(--ink-1)',
        'ink-2': 'var(--ink-2)',
        'ink-3': 'var(--ink-3)',
        'ink-4': 'var(--ink-4)',
        'ink-5': 'var(--ink-5)',
        line: 'var(--line)',
        'line-soft': 'var(--line-soft)',
        accent: 'var(--accent)',
        'accent-bg': 'var(--accent-bg)',
        pos: 'var(--pos)',
        'pos-bg': 'var(--pos-bg)',
        neg: 'var(--neg)',
        'neg-bg': 'var(--neg-bg)',
        warn: 'var(--warn)',
        'warn-bg': 'var(--warn-bg)',
      },
      fontFamily: {
        ui: ['Inter', 'sans-serif'],
        mono: ['"JetBrains Mono"', 'monospace'],
      },
    },
  },
  plugins: [],
} satisfies Config;
