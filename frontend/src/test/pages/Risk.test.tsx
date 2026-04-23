import { render } from '@testing-library/react';
import { vi } from 'vitest';
import { Wrapper } from '../helpers';
import { Risk } from '@/pages/Risk';

vi.mock('@/lib/api', () => ({
  api: {
    getRisk: vi.fn().mockResolvedValue({
      current: null,
      history: [],
      bot: { paused: false, kill_switch: false, options_enabled: true, approve_mode: true, last_heartbeat: null },
      risk_config: { max_drawdown_pct: 50, max_risk_per_trade_pct: 5, max_positions: 5, require_positive_cash: true },
      positions_used: 0,
      positions_max: 5,
    }),
  },
}));

it('renders Risk without crashing', () => {
  const { container } = render(<Risk />, { wrapper: Wrapper });
  expect(container).toBeTruthy();
});
