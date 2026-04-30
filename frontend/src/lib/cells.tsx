export function symbolCell(r: { symbol: string; name?: string }) {
  return (
    <span style={{ display: 'inline-flex', alignItems: 'baseline', gap: 4, flexWrap: 'wrap' }}>
      <strong style={{ color: 'var(--ink-1)' }}>{r.name || r.symbol}</strong>
      {r.name && <span style={{ color: 'var(--ink-4)', fontSize: '0.82em' }}>[{r.symbol}]</span>}
    </span>
  );
}
