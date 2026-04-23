export function fmtMoney(v: number, currency = 'USD'): string {
  return new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency,
    minimumFractionDigits: 0,
    maximumFractionDigits: 0,
  }).format(v);
}

export function fmtCompact(v: number): string {
  return new Intl.NumberFormat('en-US', {
    notation: 'compact',
    maximumFractionDigits: 1,
  }).format(v);
}

export function fmtPct(v: number, decimals = 2): string {
  return `${v.toFixed(decimals)}%`;
}

export function fmtSign(v: number, decimals = 2): string {
  return `${v >= 0 ? '+' : ''}${v.toFixed(decimals)}`;
}

export function fmtSignMoney(v: number): string {
  const abs = Math.abs(v);
  const prefix = v >= 0 ? '+' : '-';
  return `${prefix}${fmtMoney(abs)}`;
}

export function fmtTs(ts: string): string {
  return new Date(ts).toLocaleString('en-US', {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  });
}

export function fmtDate(ts: string): string {
  return new Date(ts).toLocaleDateString('en-US', {
    month: 'short',
    day: 'numeric',
  });
}

export function fmtDateShort(ts: string): string {
  return new Date(ts).toLocaleDateString('en-US', { month: 'numeric', day: 'numeric' });
}

export function colorForPnl(v: number): string {
  if (v > 0) return 'var(--pos)';
  if (v < 0) return 'var(--neg)';
  return 'var(--ink-3)';
}

export function colorForScore(v: number): string {
  if (v >= 0.55) return 'var(--pos)';
  if (v <= 0.45) return 'var(--neg)';
  return 'var(--warn)';
}
