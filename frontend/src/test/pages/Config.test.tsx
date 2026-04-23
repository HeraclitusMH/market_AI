import { render } from '@testing-library/react';
import { vi } from 'vitest';
import { Wrapper } from '../helpers';
import { Config } from '@/pages/Config';

vi.mock('@/lib/api', () => ({
  api: {
    getConfig: vi.fn().mockResolvedValue({
      sections: {
        General: { mode: 'PAPER', dry_run: false },
        Risk: { max_drawdown_pct: 50, max_positions: 5 },
      },
    }),
  },
}));

it('renders Config without crashing', () => {
  const { container } = render(<Config />, { wrapper: Wrapper });
  expect(container).toBeTruthy();
});
