import { Outlet } from 'react-router-dom';
import { Sidebar } from './Sidebar';
import { Topbar } from './Topbar';
import { TweaksPanel } from './TweaksPanel';
import { useTweaks } from '@/hooks/useTweaks';

export function AppShell() {
  useTweaks();
  return (
    <div className="app-shell">
      <Sidebar />
      <div className="main-col">
        <Topbar />
        <div className="content">
          <div className="content-inner">
            <Outlet />
          </div>
        </div>
      </div>
      <TweaksPanel />
    </div>
  );
}
