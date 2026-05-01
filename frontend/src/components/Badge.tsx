import React from 'react';

type BadgeVariant = 'pos' | 'neg' | 'warn' | 'info' | 'neutral' | 'solid';

interface BadgeProps {
  children: React.ReactNode;
  variant?: BadgeVariant;
  dot?: boolean;
  title?: string;
}

const DOT_COLORS: Record<BadgeVariant, string> = {
  pos: 'var(--pos)',
  neg: 'var(--neg)',
  warn: 'var(--warn)',
  info: 'var(--accent)',
  neutral: 'var(--ink-4)',
  solid: 'var(--bg-0)',
};

export function Badge({ children, variant = 'neutral', dot, title }: BadgeProps) {
  return (
    <span className={`badge ${variant}`} title={title}>
      {dot && (
        <span
          className="badge-dot"
          style={{ background: DOT_COLORS[variant] }}
          aria-hidden="true"
        />
      )}
      {children}
    </span>
  );
}

export function statusBadge(status: string): React.ReactNode {
  const s = status.toLowerCase();
  if (s === 'filled' || s === 'complete') return <Badge variant="pos" dot>{status}</Badge>;
  if (s === 'cancelled' || s === 'rejected') return <Badge variant="neg" dot>{status}</Badge>;
  if (s === 'pending' || s === 'pending_approval') return <Badge variant="warn" dot>{status}</Badge>;
  if (s === 'submitted') return <Badge variant="info" dot>{status}</Badge>;
  return <Badge variant="neutral">{status}</Badge>;
}

export function actionBadge(action: string): React.ReactNode {
  const a = action.toLowerCase();
  if (a === 'buy' || a === 'bullish') return <Badge variant="pos">{action}</Badge>;
  if (a === 'sell' || a === 'bearish') return <Badge variant="neg">{action}</Badge>;
  if (a === 'hold') return <Badge variant="neutral">{action}</Badge>;
  return <Badge variant="info">{action}</Badge>;
}
