import { NavLink } from 'react-router-dom';
import {
  LayoutDashboard, TrendingUp, ListOrdered, Zap, BarChart2,
  MessageSquare, ShieldAlert, Settings2, Sliders, Activity,
} from 'lucide-react';
import { useBotStore } from '@/store/botStore';

interface NavItem { to: string; icon: React.ReactNode; label: string; }

const NAV_GROUPS: Array<{ label: string; items: NavItem[] }> = [
  {
    label: 'Dashboard',
    items: [
      { to: '/overview', icon: <LayoutDashboard size={15} />, label: 'Overview' },
    ],
  },
  {
    label: 'Portfolio',
    items: [
      { to: '/positions', icon: <TrendingUp size={15} />, label: 'Positions' },
      { to: '/orders', icon: <ListOrdered size={15} />, label: 'Orders' },
    ],
  },
  {
    label: 'Model',
    items: [
      { to: '/signals', icon: <Zap size={15} />, label: 'Signals' },
      { to: '/rankings', icon: <BarChart2 size={15} />, label: 'Rankings' },
      { to: '/sentiment', icon: <MessageSquare size={15} />, label: 'Sentiment' },
      { to: '/regime', icon: <Activity size={15} />, label: 'Regime' },
    ],
  },
  {
    label: 'Ops',
    items: [
      { to: '/risk', icon: <ShieldAlert size={15} />, label: 'Risk' },
      { to: '/controls', icon: <Sliders size={15} />, label: 'Controls' },
      { to: '/config', icon: <Settings2 size={15} />, label: 'Config' },
    ],
  },
];

function botStatusClass(bot: { paused: boolean; kill_switch: boolean } | null): string {
  if (!bot) return 'neutral';
  if (bot.kill_switch) return 'killed';
  if (bot.paused) return 'paused';
  return 'trading';
}

function botStatusLabel(bot: { paused: boolean; kill_switch: boolean } | null): string {
  if (!bot) return 'Offline';
  if (bot.kill_switch) return 'Kill switch';
  if (bot.paused) return 'Paused';
  return 'Trading';
}

export function Sidebar() {
  const bot = useBotStore((s) => s.bot);
  const cls = botStatusClass(bot);

  return (
    <aside className="sidebar">
      <div className="sidebar-logo">
        <div className="logo-mark" aria-hidden="true" />
        <span className="logo-name">Market AI</span>
      </div>

      {NAV_GROUPS.map((group) => (
        <nav key={group.label} className="nav-group" aria-label={group.label}>
          <div className="nav-group-label">{group.label}</div>
          {group.items.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              className={({ isActive }) => `nav-item${isActive ? ' active' : ''}`}
            >
              {item.icon}
              {item.label}
            </NavLink>
          ))}
        </nav>
      ))}

      <div className="sidebar-footer">
        <div className={`status-pill ${cls}`} role="status" aria-label={`Bot status: ${botStatusLabel(bot)}`}>
          <span className="pulse-dot" style={{ background: cls === 'trading' ? 'var(--pos)' : cls === 'paused' ? 'var(--warn)' : 'var(--neg)' }} />
          {botStatusLabel(bot)}
        </div>
      </div>
    </aside>
  );
}
