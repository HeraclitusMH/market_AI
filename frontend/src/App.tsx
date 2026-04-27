import { createBrowserRouter, RouterProvider, Navigate } from 'react-router-dom';
import { AppShell } from '@/components/AppShell';
import { Overview } from '@/pages/Overview';
import { Positions } from '@/pages/Positions';
import { Orders } from '@/pages/Orders';
import { Signals } from '@/pages/Signals';
import { Rankings } from '@/pages/Rankings';
import { Sentiment } from '@/pages/Sentiment';
import { Risk } from '@/pages/Risk';
import { Controls } from '@/pages/Controls';
import { Config } from '@/pages/Config';

const router = createBrowserRouter(
  [
    {
      path: '/',
      element: <AppShell />,
      children: [
        { index: true, element: <Navigate to="/overview" replace /> },
        { path: 'overview', element: <Overview /> },
        { path: 'positions', element: <Positions /> },
        { path: 'orders', element: <Orders /> },
        { path: 'signals', element: <Signals /> },
        { path: 'rankings', element: <Rankings /> },
        { path: 'sentiment', element: <Sentiment /> },
        { path: 'risk', element: <Risk /> },
        { path: 'controls', element: <Controls /> },
        { path: 'config', element: <Config /> },
        { path: '*', element: <Navigate to="/overview" replace /> },
      ],
    },
  ],
);

export default function App() {
  return <RouterProvider router={router} />;
}
