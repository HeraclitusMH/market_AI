import { render } from '@testing-library/react';
import { vi } from 'vitest';
import { Wrapper } from '../helpers';
import { Rankings } from '@/pages/Rankings';

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
