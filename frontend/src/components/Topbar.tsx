import { useLocation } from 'react-router-dom';
import { Bell, Search } from 'lucide-react';
import { useBotStore } from '@/store/botStore';

const PAGE_NAMES: Record<string, [string, string]> = {
  overview: ['Dashboard', 'Overview'],
  positions: ['Portfolio', 'Positions'],
  orders: ['Portfolio', 'Orders'],
  signals: ['Model', 'Signals'],
  rankings: ['Model', 'Rankings'],
  sentiment: ['Model', 'Sentiment'],
  risk: ['Ops', 'Risk'],
  controls: ['Ops', 'Controls'],
  config: ['Ops', 'Config'],
};

export function Topbar() {
  const { pathname } = useLocation();
  const segment = pathname.replace(/^\//, '').split('/')[0] || 'overview';
  const [group, page] = PAGE_NAMES[segment] ?? ['', segment];
  const bot = useBotStore((s) => s.bot);

  const isLive = bot && !bot.kill_switch && !bot.paused;

  return (
    <header className="topbar">
      <nav className="topbar-breadcrumb" aria-label="Breadcrumb">
        {group && <><span>{group}</span><span className="crumb-sep">/</span></>}
        <span className="crumb-current">{page}</span>
      </nav>

      <div className="topbar-spacer" />

      <button className="topbar-search" aria-label="Open search (⌘K)">
        <Search size={13} />
        <span>Search…</span>
        <kbd className="topbar-kbd">⌘K</kbd>
      </button>

      <div className="pulse-pill" aria-label={`Data feed: ${isLive ? 'live' : 'offline'}`}>
        <span className={`pulse-dot${isLive ? '' : ' dead'}`} />
        {isLive ? 'Live' : 'Offline'}
      </div>

      {bot && (
        <div
          className={`status-pill ${bot.kill_switch ? 'killed' : bot.paused ? 'paused' : 'trading'}`}
          role="status"
        >
          {bot.kill_switch ? 'Kill switch' : bot.paused ? 'Paused' : 'Trading'}
        </div>
      )}

      <button className="btn ghost sm" aria-label="Notifications">
        <Bell size={14} />
      </button>
    </header>
  );
}
