import { render } from '@testing-library/react';
import { vi } from 'vitest';
import { Wrapper } from '../helpers';
import { Orders } from '@/pages/Orders';

vi.mock('@/lib/api', () => ({
  api: {
    getOrders: vi.fn().mockResolvedValue([]),
    getFills: vi.fn().mockResolvedValue([]),
  },
}));

it('renders Orders without crashing', () => {
  const { container } = render(<Orders />, { wrapper: Wrapper });
  expect(container).toBeTruthy();
});
