import { render } from '@testing-library/react';
import { vi } from 'vitest';
import { Wrapper } from '../helpers';
import { Controls } from '@/pages/Controls';

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
    postControl: vi.fn().mockResolvedValue({
      ok: true,
      bot: { paused: false, kill_switch: false, options_enabled: true, approve_mode: true, last_heartbeat: null },
    }),
  },
}));

it('renders Controls without crashing', () => {
  const { container } = render(<Controls />, { wrapper: Wrapper });
  expect(container).toBeTruthy();
});
