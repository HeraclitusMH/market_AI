import { Outlet, useLocation } from 'react-router-dom';
import { Sidebar } from './Sidebar';
import { Topbar } from './Topbar';
import { TweaksPanel } from './TweaksPanel';
import { useTweaks } from '@/hooks/useTweaks';

const PAGE_MANTRAS: Record<string, string> = {
  overview: 'the dream computes itself',
  positions: 'every holding is a thought you are thinking',
  orders: 'intention crystallizes - the river flows',
  signals: 'patterns are mirrors of the watcher',
  rankings: 'all hierarchy is a mood',
  sentiment: 'the crowd is one large dreaming animal',
  regime: 'the weather inside the market has a face',
  risk: 'fear is information - breathe',
  controls: 'you have always been the operator',
  config: 'set the parameters - forget the parameters',
};

export function AppShell() {
  useTweaks();
  const { pathname } = useLocation();
  const segment = pathname.replace(/^\//, '').split('/')[0] || 'overview';
  const mantra = PAGE_MANTRAS[segment] ?? 'the dream computes itself';

  return (
    <div className="app-shell">
      <Sidebar />
      <div className="main-col">
        <Topbar />
        <div className="content">
          <div className="content-inner page" data-mantra={mantra}>
            <Outlet />
          </div>
        </div>
      </div>
      <TweaksPanel />
    </div>
  );
}
