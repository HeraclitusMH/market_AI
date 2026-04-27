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
  expect(screen.getByText(/Fundamentals missing; weights redistributed/)).toBeInTheDocument();
  expect(screen.queryByText(/Liquidity.*x/)).not.toBeInTheDocument();
  expect(screen.getByText('Missing')).toBeInTheDocument();

  await waitFor(() => {
    expect(screen.queryByText('NaN')).not.toBeInTheDocument();
  });
});

it('includes legacy weighted momentum aliases in the score formula', async () => {
  vi.mocked(api.getRankings).mockResolvedValueOnce([
    {
      id: 1,
      ts: '2026-04-27T10:00:00',
      symbol: 'SPY',
      score_total: 0.72,
      eligible: true,
      reasons: [],
      components: {
        sentiment: { value_0_1: 0.57, status: 'ok' },
        momentum: { value_0_1: 0.97, status: 'ok' },
        risk: { value_0_1: 0.75, status: 'ok' },
        fundamentals: { value_0_1: null, status: 'missing' },
        weights_used: { sentiment: 0.4615, momentum: 0.2308, risk: 0.3077, fundamentals: 0 },
      },
    },
  ]);

  render(<Rankings />, { wrapper: Wrapper });

  expect(await screen.findAllByText('SPY')).toHaveLength(2);
  expect(screen.getByText(/Sentiment 57 x 46\.2%/)).toBeInTheDocument();
  expect(screen.getByText(/Momentum 97 x 23\.1%/)).toBeInTheDocument();
  expect(screen.getByText(/Risk 75 x 30\.8%/)).toBeInTheDocument();
  expect(screen.queryByText(/stored score/)).not.toBeInTheDocument();
});

it('excludes liquidity weights from the score formula', async () => {
  vi.mocked(api.getRankings).mockResolvedValueOnce([
    {
      id: 1,
      ts: '2026-04-27T10:00:00',
      symbol: 'SPY',
      score_total: 0.64,
      eligible: true,
      reasons: [],
      components: {
        sentiment: { value_0_1: 0.57, status: 'ok' },
        risk: { value_0_1: 0.75, status: 'ok' },
        liquidity: { value_0_1: 1, status: 'ok', eligible: true },
        fundamentals: { value_0_1: null, status: 'missing' },
        weights_used: { sentiment: 0.4615, risk: 0.3077, liquidity: 0.2308, fundamentals: 0 },
      },
    },
  ]);

  render(<Rankings />, { wrapper: Wrapper });

  expect(await screen.findAllByText('SPY')).toHaveLength(2);
  expect(screen.getByText(/Sentiment 57 x 60\.0%/)).toBeInTheDocument();
  expect(screen.getByText(/Risk 75 x 40\.0%/)).toBeInTheDocument();
  expect(screen.queryByText(/liquidity 100 x/i)).not.toBeInTheDocument();
  expect(screen.queryByText(/stored score/)).not.toBeInTheDocument();
});
