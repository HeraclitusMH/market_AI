import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { api } from '@/lib/api';
import { fmtMoney, fmtSignMoney, fmtPct, colorForPnl } from '@/lib/formatters';
import { KPI } from '@/components/KPI';
import { Card, CardHead, CardBody } from '@/components/Card';
import { DataTable, type Column } from '@/components/DataTable';
import { Sparkline } from '@/components/Sparkline';
import { Badge } from '@/components/Badge';
import { SegmentedControl } from '@/components/SegmentedControl';
import type { Position } from '@/types/api';

type Filter = 'all' | 'equity' | 'options' | 'winners' | 'losers';

const FILTERS = [
  { value: 'all', label: 'All' },
  { value: 'equity', label: 'Equity' },
  { value: 'options', label: 'Options' },
  { value: 'winners', label: 'Winners' },
  { value: 'losers', label: 'Losers' },
];

const COLS: Column<Position>[] = [
  { key: 'symbol', header: 'Symbol', render: (r) => <strong style={{ color: 'var(--ink-1)' }}>{r.symbol}</strong> },
  { key: 'instrument', header: 'Type', render: (r) => <Badge variant="info">{r.instrument}</Badge> },
  { key: 'quantity', header: 'Qty', numeric: true },
  { key: 'avg_cost', header: 'Avg Cost', numeric: true, render: (r) => fmtMoney(r.avg_cost) },
  { key: 'market_price', header: 'Price', numeric: true, render: (r) => fmtMoney(r.market_price) },
  { key: 'market_value', header: 'Mkt Value', numeric: true, render: (r) => fmtMoney(r.market_value) },
  {
    key: 'unrealized_pnl', header: 'Unreal P&L', numeric: true,
    render: (r) => <span style={{ color: colorForPnl(r.unrealized_pnl) }}>{fmtSignMoney(r.unrealized_pnl)}</span>,
  },
  {
    key: '_pct_chg', header: '% Chg', numeric: true, sortable: false,
    render: (r) => {
      if (!r.avg_cost) return '—';
      const pct = ((r.market_price - r.avg_cost) / r.avg_cost) * 100;
      return <span style={{ color: colorForPnl(pct) }}>{fmtPct(pct)}</span>;
    },
  },
  {
    key: '_spark', header: '', sortable: false,
    render: (r) => (
      <Sparkline
        data={[r.avg_cost, r.market_price]}
        color={r.unrealized_pnl >= 0 ? 'var(--pos)' : 'var(--neg)'}
      />
    ),
  },
];

function applyFilter(data: Position[], filter: Filter): Position[] {
  switch (filter) {
    case 'equity': return data.filter((p) => p.instrument === 'STK');
    case 'options': return data.filter((p) => p.instrument !== 'STK');
    case 'winners': return data.filter((p) => p.unrealized_pnl > 0);
    case 'losers': return data.filter((p) => p.unrealized_pnl < 0);
    default: return data;
  }
}

export function Positions() {
  const { data = [], isLoading } = useQuery({ queryKey: ['positions'], queryFn: api.getPositions, refetchInterval: 15_000 });
  const [filter, setFilter] = useState<Filter>('all');

  const filtered = applyFilter(data, filter);
  const totalValue = data.reduce((s, p) => s + p.market_value, 0);
  const totalUnreal = data.reduce((s, p) => s + p.unrealized_pnl, 0);
  const winners = data.filter((p) => p.unrealized_pnl > 0).length;
  const losers = data.filter((p) => p.unrealized_pnl < 0).length;

  if (isLoading) return <div className="loading-state">Loading positions…</div>;

  return (
    <div>
      <h1 className="page-title">Positions</h1>

      <div className="kpi-grid">
        <KPI label="Open Positions" value={String(data.length)} />
        <KPI label="Total Mkt Value" value={fmtMoney(totalValue)} />
        <KPI label="Unrealized P&L" value={fmtSignMoney(totalUnreal)} color={totalUnreal >= 0 ? 'pos' : 'neg'} />
        <KPI label="Win / Loss" value={`${winners} / ${losers}`} sub={data.length ? `${Math.round(winners / data.length * 100)}% win rate` : undefined} />
      </div>

      <Card>
        <CardHead
          title="Positions"
          subtitle={`${filtered.length} shown`}
          right={<SegmentedControl options={FILTERS} value={filter} onChange={(v) => setFilter(v as Filter)} aria-label="Filter positions" />}
        />
        <CardBody flush>
          <DataTable
            data={filtered as unknown as Record<string, unknown>[]}
            columns={COLS as unknown as Column<Record<string, unknown>>[]}
            emptyMessage="No positions match this filter"
          />
        </CardBody>
      </Card>
    </div>
  );
}
