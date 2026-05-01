import { render, screen } from '@testing-library/react';
import { vi } from 'vitest';
import { Wrapper } from '../helpers';
import { Overview } from '@/pages/Overview';

vi.mock('@/lib/api', () => ({
  api: {
    getOverview: vi.fn().mockResolvedValue({
      bot: { paused: false, kill_switch: false, options_enabled: true, approve_mode: true, last_heartbeat: null },
      equity: null,
      equity_history: [],
      positions: [],
      position_count: 0,
      sentiment_provider: 'rss_lexicon',
      sentiment_llm_budget: null,
      recent_events: [],
    }),
    getRegimeCurrent: vi.fn().mockResolvedValue({ level: 'risk_on', composite_score: 72 }),
  },
}));

it('renders Overview without crashing', () => {
  const { container } = render(<Overview />, { wrapper: Wrapper });
  expect(container).toBeTruthy();
});

it('shows loading state initially', () => {
  render(<Overview />, { wrapper: Wrapper });
  expect(screen.getByText(/loading/i)).toBeTruthy();
});
