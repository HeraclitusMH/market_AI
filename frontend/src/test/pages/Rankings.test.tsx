import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { vi } from 'vitest';
import { Wrapper } from '../helpers';
import { Rankings } from '@/pages/Rankings';
import { api } from '@/lib/api';

vi.mock('@/lib/api', () => ({
  api: {
    getRankings: vi.fn().mockResolvedValue([]),
    getTradePlans: vi.fn().mockResolvedValue([]),
    getRegimeCurrent: vi.fn().mockResolvedValue({ level: 'risk_on', composite_score: 70 }),
  },
}));

function composite(score = 72) {
  return {
    composite_score: score,
    regime: 'rotation_choppy',
    confidence: 0.88,
    factors: {
      quality: { score: 70, weight: 0.2, contribution: 14, components: {} },
      value: { score: 65, weight: 0.15, contribution: 9.75, components: {} },
      momentum: { score: 80, weight: 0.1, contribution: 8, components: {} },
      growth: { score: 60, weight: 0.15, contribution: 9, components: {} },
      sentiment: { score: 75, weight: 0.2, contribution: 15, components: {} },
      technical: { score: 85, weight: 0.15, contribution: 12.75, components: {} },
      risk: { score: 35, weight: 0.05, contribution: -1.75, components: {} },
    },
  };
}

it('renders Rankings without crashing', () => {
  const { container } = render(<Rankings />, { wrapper: Wrapper });
  expect(container).toBeTruthy();
});

it('renders factor breakdown scores from the 7-factor composite payload', async () => {
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
        weights_used: { quality: 0.2, value: 0.15, momentum: 0.1, growth: 0.15, sentiment: 0.2, technical: 0.15, risk: 0.05 },
        composite_7factor: composite(72),
      },
    },
  ]);

  render(<Rankings />, { wrapper: Wrapper });

  expect(await screen.findAllByText('AAPL')).toHaveLength(2);
  fireEvent.click(screen.getAllByText('AAPL')[1].closest('tr') as HTMLElement);
  expect(screen.getByText('Sentiment')).toBeInTheDocument();
  expect(screen.getByText('+15.00')).toBeInTheDocument();
  expect(screen.getByText('Liquidity Gate')).toBeInTheDocument();
  expect(screen.getByText('Pass')).toBeInTheDocument();
  expect(screen.getByText('Formula')).toBeInTheDocument();
  expect(screen.getAllByText((_, el) => Boolean(el?.textContent?.includes('Sentiment 75') && el.textContent.includes('20%'))).length).toBeGreaterThan(0);
  expect(screen.getAllByText((_, el) => Boolean(el?.textContent?.includes('Risk Penalty 35') && el.textContent.includes('5%'))).length).toBeGreaterThan(0);
  expect(screen.queryByText(/Liquidity.*x/)).not.toBeInTheDocument();

  await waitFor(() => {
    expect(screen.queryByText('NaN')).not.toBeInTheDocument();
  });
});

it('does not render the old weighted factor formula when composite payload is missing', async () => {
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
  fireEvent.click(screen.getAllByText('SPY')[1].closest('tr') as HTMLElement);
  expect(screen.getByText(/Missing 7-factor composite payload/)).toBeInTheDocument();
  expect(screen.queryByText(/Sentiment 57 x 46\.2%/)).not.toBeInTheDocument();
  expect(screen.queryByText(/Momentum 97 x 23\.1%/)).not.toBeInTheDocument();
  expect(screen.queryByText(/Risk 75 x 30\.8%/)).not.toBeInTheDocument();
  expect(screen.queryByText('Formula')).not.toBeInTheDocument();
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
        composite_7factor: composite(64),
      },
    },
  ]);

  render(<Rankings />, { wrapper: Wrapper });

  expect(await screen.findAllByText('SPY')).toHaveLength(2);
  fireEvent.click(screen.getAllByText('SPY')[1].closest('tr') as HTMLElement);
  expect(screen.getAllByText((_, el) => Boolean(el?.textContent?.includes('Sentiment 75') && el.textContent.includes('20%'))).length).toBeGreaterThan(0);
  expect(screen.getAllByText((_, el) => Boolean(el?.textContent?.includes('Risk Penalty 35') && el.textContent.includes('5%'))).length).toBeGreaterThan(0);
  expect(screen.queryByText(/liquidity 100 x/i)).not.toBeInTheDocument();
});
