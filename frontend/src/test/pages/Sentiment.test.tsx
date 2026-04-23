import { render } from '@testing-library/react';
import { vi } from 'vitest';
import { Wrapper } from '../helpers';
import { Sentiment } from '@/pages/Sentiment';

vi.mock('@/lib/api', () => ({
  api: {
    getSentiment: vi.fn().mockResolvedValue({
      market: null,
      sectors: [],
      tickers: [],
      headlines: [],
      history: [],
      budget: {
        provider: 'rss_lexicon', model: null,
        month_to_date_eur: 0, today_eur: 0,
        monthly_cap_eur: 10, daily_cap_eur: 1.2,
        remaining_month_eur: 10, remaining_today_eur: 1.2,
        budget_stopped: false, reason: null,
      },
      provider: 'rss_lexicon',
    }),
    refreshSentiment: vi.fn().mockResolvedValue({ status: 'ok', snapshots_written: 0, reason: '' }),
  },
}));

it('renders Sentiment without crashing', () => {
  const { container } = render(<Sentiment />, { wrapper: Wrapper });
  expect(container).toBeTruthy();
});
