import React from 'react';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

export function makeQC() {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0, staleTime: 0 },
    },
  });
}

export function Wrapper({ children }: { children: React.ReactNode }) {
  const qc = makeQC();
  return (
    <MemoryRouter>
      <QueryClientProvider client={qc}>{children}</QueryClientProvider>
    </MemoryRouter>
  );
}
