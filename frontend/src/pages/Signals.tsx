import { useState, useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';
import { api } from '@/lib/api';
import { fmtTs } from '@/lib/formatters';
import { KPI } from '@/components/KPI';
import { Card, CardHead, CardBody } from '@/components/Card';
import { DataTable, type Column } from '@/components/DataTable';
import { SegmentedControl } from '@/components/SegmentedControl';
import { symbolCell } from '@/lib/cells';
import { ScoreBar } from '@/components/ScoreBar';
import { actionBadge } from '@/components/Badge';
import type { Signal } from '@/types/api';

type Filter = 'all' | 'buy' | 'hold' | 'bearish';
const FILTERS = [
  { value: 'all', label: 'All' },
  { value: 'buy', label: 'Buy' },
  { value: 'hold', label: 'Hold' },
  { value: 'bearish', label: 'Bearish' },
];

const COLS: Column<Signal>[] = [
  { key: 'timestamp', header: 'Time', render: (r) => <span className="mono" style={{ fontSize: 11.5 }}>{fmtTs(r.timestamp)}</span> },
  { key: 'symbol', header: 'Company', render: (r) => symbolCell(r) },
  { key: 'action', header: 'Action', render: (r) => actionBadge(r.action) },
  { key: 'regime', header: 'Regime' },
  {
    key: 'score_total', header: 'Score', numeric: true,
    render: (r) => <ScoreBar value={r.score_total} />,
  },
  { key: 'explanation', header: 'Explanation', sortable: false, render: (r) => <span style={{ color: 'var(--ink-3)', fontSize: 12 }}>{r.explanation?.slice(0, 80)}{(r.explanation?.length ?? 0) > 80 ? '…' : ''}</span> },
];

function applyFilter(data: Signal[], filter: Filter): Signal[] {
  if (filter === 'all') return data;
  const f = filter.toLowerCase();
  return data.filter((s) => s.action.toLowerCase().includes(f));
}

export function Signals() {
  const { data = [], isLoading } = useQuery({ queryKey: ['signals'], queryFn: () => api.getSignals(100), refetchInterval: 20_000 });
  const [filter, setFilter] = useState<Filter>('all');

  const filtered = useMemo(() => applyFilter(data, filter), [data, filter]);
  if (isLoading) return <div className="loading-state">Loading signals…</div>;

  const avgScore = data.length ? data.reduce((s, r) => s + r.score_total, 0) / data.length : 0;
  const bullCount = data.filter((s) => s.score_total >= 0.55).length;
  const bearCount = data.filter((s) => s.score_total <= 0.45).length;

  return (
    <div>
      <h1 className="page-title">Signals</h1>

      <div className="kpi-grid">
        <KPI label="Total Signals" value={String(data.length)} />
        <KPI label="Avg Score" value={(avgScore * 100).toFixed(0)} sub="/ 100" />
        <KPI label="Bullish" value={String(bullCount)} color="pos" />
        <KPI label="Bearish" value={String(bearCount)} color="neg" />
      </div>

      <Card>
        <CardHead
          title="Signals"
          subtitle={`${filtered.length} shown`}
          right={<SegmentedControl options={FILTERS} value={filter} onChange={(v) => setFilter(v as Filter)} aria-label="Filter signals" />}
        />
        <CardBody flush>
          <DataTable
            data={filtered as unknown as Record<string, unknown>[]}
            columns={COLS as unknown as Column<Record<string, unknown>>[]}
            emptyMessage="No signals match this filter"
          />
        </CardBody>
      </Card>
    </div>
  );
}
