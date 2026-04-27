import { render, screen, waitFor } from '@testing-library/react';
import { vi } from 'vitest';
import { Wrapper } from '../helpers';
import { Rankings } from '@/pages/Rankings';
import { api } from '@/lib/api';

vi.mock('@/lib/api', () => ({
  api: {
    getRankings: vi.fn().mockResolvedValue([]),
    getTradePlans: vi.fn().mockResolvedValue([]),
  },
}));

it('renders Rankings without crashing', () => {
  const { container } = render(<Rankings />, { wrapper: Wrapper });
  expect(container).toBeTruthy();
});

it('renders factor breakdown scores from value_0_1 objects', async () => {
  vi.mocked(api.getRankings).mockResolvedValueOnce([
    {
      id: 1,
      ts: '2026-04-27T10:00:00',
      symbol: 'AAPL',
      score_total: 0.72,
      eligible: true,
      reasons: [],
      components: {
        sentiment: { value_0_1: 0.8, status: 'ok' },
        momentum_trend: { value_0_1: 0.65, status: 'ok' },
        risk: { value_0_1: 0.7, status: 'ok' },
        liquidity: {
          value_0_1: 0,
          status: 'ok',
          eligible: true,
          metrics: { last_price: 180, adv_dollar_20d: 50_000_000 },
        },
        fundamentals: { value_0_1: null, status: 'missing' },
        weights_used: { sentiment: 0.4, momentum_trend: 0.3333, risk: 0.2667, fundamentals: 0 },
      },
    },
  ]);

  render(<Rankings />, { wrapper: Wrapper });

  expect(await screen.findAllByText('AAPL')).toHaveLength(2);
  expect(screen.getByText('Sentiment')).toBeInTheDocument();
  expect(screen.getByText('80')).toBeInTheDocument();
  expect(screen.getByText('Liquidity Gate')).toBeInTheDocument();
  expect(screen.getByText('Pass')).toBeInTheDocument();
  expect(screen.getByText(/Formula:/)).toBeInTheDocument();
  expect(screen.getByText(/Sentiment 80 x 40\.0%/)).toBeInTheDocument();
  expect(screen.queryByText(/Liquidity.*x/)).not.toBeInTheDocument();
  expect(screen.getByText('--')).toBeInTheDocument();

  await waitFor(() => {
    expect(screen.queryByText('NaN')).not.toBeInTheDocument();
  });
});
