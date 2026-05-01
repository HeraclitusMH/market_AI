import { useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';
import { api } from '@/lib/api';
import { Card, CardBody, CardHead } from '@/components/Card';
import { DataTable, type Column } from '@/components/DataTable';
import { KPI } from '@/components/KPI';
import { LineChart } from '@/components/LineChart';
import { RegimeBadge, RegimeSummaryCard } from '@/components/RegimeSummaryCard';
import { fmtDateShort, fmtTs } from '@/lib/formatters';
import type { RegimeHistoryRow, RegimePillars } from '@/types/api';

const PILLAR_LABELS: Array<[keyof RegimePillars, string]> = [
  ['trend', 'Trend'],
  ['breadth', 'Breadth'],
  ['volatility', 'Volatility'],
  ['credit_stress', 'Credit Stress'],
];

const HISTORY_COLS: Column<RegimeHistoryRow>[] = [
  { key: 'timestamp', header: 'Time', render: (r) => <span className="mono" style={{ fontSize: 11.5 }}>{fmtTs(r.timestamp)}</span> },
  { key: 'level', header: 'Regime', render: (r) => <RegimeBadge level={r.level} /> },
  { key: 'composite_score', header: 'Score', numeric: true, render: (r) => Math.round(r.composite_score) },
  { key: 'trend', header: 'Trend', numeric: true, render: (r) => r.trend?.toFixed(0) ?? '--' },
  { key: 'breadth', header: 'Breadth', numeric: true, render: (r) => r.breadth?.toFixed(0) ?? '--' },
  { key: 'volatility', header: 'Vol', numeric: true, render: (r) => r.volatility?.toFixed(0) ?? '--' },
  { key: 'credit_stress', header: 'Credit', numeric: true, render: (r) => r.credit_stress?.toFixed(0) ?? '--' },
  { key: 'transition', header: 'Transition', render: (r) => r.transition ?? '-' },
];

export function Regime() {
  const { data: current, isLoading: currentLoading } = useQuery({
    queryKey: ['regimeCurrent'],
    queryFn: api.getRegimeCurrent,
    refetchInterval: 20_000,
  });
  const { data: history = [], isLoading: historyLoading } = useQuery({
    queryKey: ['regimeHistory', 30],
    queryFn: () => api.getRegimeHistory(30),
    refetchInterval: 60_000,
  });

  const chartData = useMemo(() => history.map((row) => ({
    x: fmtDateShort(row.timestamp),
    score: row.composite_score,
    trend: row.trend ?? undefined,
    breadth: row.breadth ?? undefined,
    volatility: row.volatility ?? undefined,
    credit: row.credit_stress ?? undefined,
  })), [history]);

  const pillars = current?.pillars;

  if (currentLoading || historyLoading) return <div className="loading-state">Loading regime...</div>;

  return (
    <div>
      <h1 className="page-title">Regime</h1>

      <div className="kpi-grid">
        <KPI label="Composite Score" value={current?.composite_score == null ? '--' : current.composite_score.toFixed(0)} sub="/ 100" />
        <KPI label="Data Quality" value={current?.data_quality ?? 'unknown'} />
        <KPI label="Transition" value={current?.transition ?? 'maintained'} />
        <KPI label="History Points" value={String(history.length)} sub="30 days" />
      </div>

      <div className="grid-2">
        <RegimeSummaryCard regime={current} />
        <Card>
          <CardHead title="Pillars" />
          <CardBody>
            <div className="regime-pillar-grid">
              {PILLAR_LABELS.map(([key, label]) => {
                const value = pillars?.[key];
                return (
                  <div key={key} className="regime-pillar">
                    <span>{label}</span>
                    <strong className="mono">{value == null ? '--' : value.toFixed(0)}</strong>
                  </div>
                );
              })}
            </div>
          </CardBody>
        </Card>
      </div>

      <Card style={{ marginBottom: 'var(--gap)' }}>
        <CardHead title="30-day Regime Score" subtitle="Composite and pillar scores" />
        <CardBody>
          <LineChart
            data={chartData}
            lines={[
              { key: 'score', name: 'Composite', color: 'var(--accent)' },
              { key: 'trend', name: 'Trend', color: 'var(--pos)' },
              { key: 'breadth', name: 'Breadth', color: 'oklch(0.78 0.14 205)' },
              { key: 'volatility', name: 'Volatility', color: 'var(--warn)' },
              { key: 'credit', name: 'Credit', color: 'var(--neg)' },
            ]}
            height={280}
          />
        </CardBody>
      </Card>

      <Card>
        <CardHead title="Regime History" subtitle={`${history.length} snapshots`} />
        <CardBody flush>
          <DataTable
            data={history as unknown as Record<string, unknown>[]}
            columns={HISTORY_COLS as unknown as Column<Record<string, unknown>>[]}
            emptyMessage="No regime history"
            maxHeight={360}
          />
        </CardBody>
      </Card>
    </div>
  );
}
