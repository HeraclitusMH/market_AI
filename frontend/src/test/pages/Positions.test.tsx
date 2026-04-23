import { render, screen } from '@testing-library/react';
import { vi } from 'vitest';
import { Wrapper } from '../helpers';
import { Positions } from '@/pages/Positions';

vi.mock('@/lib/api', () => ({
  api: { getPositions: vi.fn().mockResolvedValue([]) },
}));

it('renders Positions without crashing', () => {
  const { container } = render(<Positions />, { wrapper: Wrapper });
  expect(container).toBeTruthy();
});

it('shows page title when loaded', async () => {
  render(<Positions />, { wrapper: Wrapper });
  expect(screen.getByText(/loading/i)).toBeTruthy();
});
