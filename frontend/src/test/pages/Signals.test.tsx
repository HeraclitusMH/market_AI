import { render } from '@testing-library/react';
import { vi } from 'vitest';
import { Wrapper } from '../helpers';
import { Signals } from '@/pages/Signals';

vi.mock('@/lib/api', () => ({
  api: { getSignals: vi.fn().mockResolvedValue([]) },
}));

it('renders Signals without crashing', () => {
  const { container } = render(<Signals />, { wrapper: Wrapper });
  expect(container).toBeTruthy();
});
