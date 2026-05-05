import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { vi } from 'vitest';
import { Wrapper } from '../helpers';
import { ScoreControl } from '@/pages/ScoreControl';
import { api } from '@/lib/api';

vi.mock('@/lib/api', () => ({
  api: {
    getRankings: vi.fn().mockResolvedValue([]),
    getScoringWeights: vi.fn().mockResolvedValue({
      profiles: {
        aggressive_swing: { technical: 0.30, momentum: 0.25, sentiment: 0.20, quality: 0.05, growth: 0.05, risk_penalty: 0.15 },
        defensive_swing:  { technical: 0.25, momentum: 0.20, sentiment: 0.15, quality: 0.10, growth: 0.10, risk_penalty: 0.20 },
      },
      active_profile: 'defensive_swing',
      active_weights: { technical: 0.25, momentum: 0.20, sentiment: 0.15, quality: 0.10, growth: 0.10, risk_penalty: 0.20 },
      regime_level: 'risk_reduced',
    }),
    getScoringDocs: vi.fn().mockResolvedValue({
      active_profile: 'defensive_swing',
      regime_level: 'risk_reduced',
      parameters: [
        {
          key: 'technical', label: 'Technical', weight: 0.25,
          what: 'Technical setup quality.', how: 'EMA + RSI + MACD',
          source: 'IBKR daily bars', subscores: ['EMA stack'],
          score_min: 0, score_max: 100, inverted: false,
          bands: [['80-100', 'Excellent']].map(([range, label]) => ({ range, label })),
          refresh_via: 'rankings', refresh_note: 'Recomputed every Rankings refresh.',
        },
        {
          key: 'risk_penalty', label: 'Risk Penalty', weight: 0.20,
          what: 'Subtractive penalty.', how: 'Realized vol + ATR',
          source: 'IBKR daily bars', subscores: ['Realized vol'],
          score_min: 0, score_max: 100, inverted: true,
          bands: [['0-19', 'Low risk']].map(([range, label]) => ({ range, label })),
          refresh_via: 'rankings', refresh_note: 'Recomputed every Rankings refresh.',
        },
      ],
    }),
    getRefreshHistory: vi.fn().mockResolvedValue([
      { id: 1, timestamp: '2026-05-05T10:00:00Z', action: 'rankings', status: 'success', duration_ms: 4500, message: 'ranked=120 sentiment=success' },
    ]),
    refreshRankings: vi.fn().mockResolvedValue({ status: 'success', sentiment_status: 'success', snapshots_written: 1, ranked: 120, latest_ts: '2026-05-05T10:00:00' }),
    refreshSentiment: vi.fn().mockResolvedValue({ status: 'success', snapshots_written: 5, reason: '' }),
    refreshFundamentals: vi.fn().mockResolvedValue({ refreshed: 100, missing: 0, errors: [], duration_s: 12, symbols: [] }),
  },
}));

it('renders ScoreControl without crashing', () => {
  const { container } = render(<ScoreControl />, { wrapper: Wrapper });
  expect(container).toBeTruthy();
});

it('shows the three tabs and Controls is the default', async () => {
  render(<ScoreControl />, { wrapper: Wrapper });
  expect(screen.getByRole('tab', { name: 'Controls' })).toHaveAttribute('aria-selected', 'true');
  expect(screen.getByRole('tab', { name: 'Documentation' })).toBeInTheDocument();
  expect(screen.getByRole('tab', { name: 'Rankings' })).toBeInTheDocument();
  // The phrase appears as both the card title and inside the help paragraph.
  const matches = await screen.findAllByText('Refresh All Rankings');
  expect(matches.length).toBeGreaterThanOrEqual(1);
});

it('triggers the rankings refresh mutation when the button is clicked', async () => {
  render(<ScoreControl />, { wrapper: Wrapper });
  // The card title and the inline help text both mention "Refresh All Rankings",
  // so pick the one that lives inside .card-title.
  const titles = await screen.findAllByText('Refresh All Rankings');
  const titleEl = titles.find((el) => el.classList.contains('card-title')) ?? titles[0];
  const card = titleEl.closest('.card') as HTMLElement;
  const button = card.querySelector('button') as HTMLButtonElement;
  expect(button).toBeTruthy();
  fireEvent.click(button);
  await waitFor(() => expect(api.refreshRankings).toHaveBeenCalledTimes(1));
});

it('renders parameter docs with weights pulled from the API', async () => {
  render(<ScoreControl />, { wrapper: Wrapper });
  fireEvent.click(screen.getByRole('tab', { name: 'Documentation' }));
  // "Technical" appears as a card title here — make sure at least one is rendered.
  const technicalTitles = await screen.findAllByText('Technical');
  expect(technicalTitles.some((el) => el.classList.contains('card-title'))).toBe(true);
  // Weight labels rendered as the big number in CardHead right slot.
  expect(screen.getByText('25%')).toBeInTheDocument();
  expect(screen.getByText('20%')).toBeInTheDocument();
  // Risk Penalty card is inverted — the "inverted (lower = better)" copy must appear.
  expect(screen.getByText(/inverted/)).toBeInTheDocument();
});

it('renders the rankings tab and respects the search filter', async () => {
  vi.mocked(api.getRankings).mockResolvedValueOnce([
    {
      id: 1, ts: '2026-05-05T10:00:00', symbol: 'AAPL', name: 'Apple Inc.',
      score_total: 0.78, eligible: true, reasons: [],
      components: {
        composite_6factor: {
          composite_score: 0.78, regime: 'risk_reduced', weight_profile: 'defensive_swing', confidence: 0.9,
          factors: {
            technical:    { score: 0.85, weight: 0.25, contribution: 0.21, components: {} },
            momentum:     { score: 0.80, weight: 0.20, contribution: 0.16, components: {} },
            sentiment:    { score: 0.70, weight: 0.15, contribution: 0.10, components: {} },
            quality:      { score: 0.65, weight: 0.10, contribution: 0.06, components: {} },
            growth:       { score: 0.60, weight: 0.10, contribution: 0.06, components: {} },
            risk_penalty: { score: 0.30, weight: 0.20, contribution: -0.06, components: {} },
          },
        },
      },
    },
    {
      id: 2, ts: '2026-05-05T10:00:00', symbol: 'MSFT', name: 'Microsoft',
      score_total: 0.62, eligible: true, reasons: [],
      components: { composite_6factor: { composite_score: 0.62, regime: 'risk_reduced', weight_profile: 'defensive_swing', confidence: 0.9, factors: {} } },
    },
  ]);

  render(<ScoreControl />, { wrapper: Wrapper });
  fireEvent.click(screen.getByRole('tab', { name: 'Rankings' }));

  expect(await screen.findByText('AAPL')).toBeInTheDocument();
  expect(screen.getByText('MSFT')).toBeInTheDocument();
  expect(screen.getByText('78')).toBeInTheDocument();

  fireEvent.change(screen.getByLabelText('Filter ticker or name'), { target: { value: 'app' } });
  await waitFor(() => {
    expect(screen.getByText('AAPL')).toBeInTheDocument();
    expect(screen.queryByText('MSFT')).not.toBeInTheDocument();
  });
});
