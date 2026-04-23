import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { api } from '@/lib/api';
import { fmtTs } from '@/lib/formatters';
import { KPI } from '@/components/KPI';
import { Card, CardHead, CardBody } from '@/components/Card';
import { DataTable, type Column } from '@/components/DataTable';
import { ScoreBar, FactorBar } from '@/components/ScoreBar';
import { Badge } from '@/components/Badge';
import type { RankingRow, PlanRow } from '@/types/api';

const FACTOR_KEYS: [string, string][] = [
  ['sentiment', 'Sentiment'],
  ['momentum_trend', 'Momentum'],
  ['risk', 'Risk'],
  ['liquidity', 'Liquidity'],
  ['optionability', 'Options'],
  ['fundamentals', 'Fundamentals'],
];

function FactorBreakdown({ components }: { components: Record<string, number> }) {
  return (
    <div style={{ padding: '8px 12px 12px' }}>
      {FACTOR_KEYS.map(([k, label]) => {
        const v = components[k];
        if (v === undefined) return null;
        return <FactorBar key={k} name={label} value={v} />;
      })}
    </div>
  );
}

const RANKING_COLS: Column<RankingRow>[] = [
  { key: 'symbol', header: 'Symbol', render: (r) => <strong style={{ color: 'var(--ink-1)' }}>{r.symbol}</strong> },
  { key: 'score_total', header: 'Score', numeric: true, render: (r) => <ScoreBar value={r.score_total} /> },
  {
    key: 'eligible', header: 'Eligible', sortable: false,
    render: (r) => <Badge variant={r.eligible ? 'pos' : 'neutral'} dot>{r.eligible ? 'Yes' : 'No'}</Badge>,
  },
  {
    key: '_factors', header: 'Factors', sortable: false,
    render: (r) => (
      <details style={{ cursor: 'pointer' }}>
        <summary style={{ fontSize: 11.5, color: 'var(--accent)' }}>View breakdown</summary>
        <FactorBreakdown components={r.components} />
      </details>
    ),
  },
];

const PLAN_COLS: Column<PlanRow>[] = [
  { key: 'ts', header: 'Time', render: (r) => <span className="mono" style={{ fontSize: 11.5 }}>{fmtTs(r.ts)}</span> },
  { key: 'symbol', header: 'Symbol', render: (r) => <strong style={{ color: 'var(--ink-1)' }}>{r.symbol}</strong> },
  { key: 'bias', header: 'Bias', render: (r) => <Badge variant={r.bias === 'bullish' ? 'pos' : 'neg'}>{r.bias}</Badge> },
  { key: 'strategy', header: 'Strategy' },
  { key: 'expiry', header: 'Expiry', render: (r) => r.expiry ?? '—' },
  { key: 'dte', header: 'DTE', numeric: true, render: (r) => r.dte != null ? String(r.dte) : '—' },
  { key: 'status', header: 'Status', render: (r) => <Badge variant={r.status === 'pending' ? 'warn' : r.status === 'submitted' ? 'info' : 'neutral'} dot>{r.status}</Badge> },
];

export function Rankings() {
  const { data: rankings = [], isLoading: rankLoading } = useQuery({ queryKey: ['rankings'], queryFn: () => api.getRankings(100), refetchInterval: 30_000 });
  const { data: plans = [], isLoading: planLoading } = useQuery({ queryKey: ['tradePlans'], queryFn: () => api.getTradePlans(50), refetchInterval: 30_000 });

  const [activeTab, setActiveTab] = useState<'all' | 'bullish' | 'bearish'>('all');

  if (rankLoading || planLoading) return <div className="loading-state">Loading rankings…</div>;

  const bullish = rankings.filter((r) => r.eligible && r.score_total >= 0.55).slice(0, 10);
  const bearish = rankings.filter((r) => r.eligible && r.score_total <= 0.45).sort((a, b) => a.score_total - b.score_total).slice(0, 10);
  const displayed = activeTab === 'bullish' ? bullish : activeTab === 'bearish' ? bearish : rankings;

  const avgScore = rankings.length ? rankings.reduce((s, r) => s + r.score_total, 0) / rankings.length : 0;
  const eligible = rankings.filter((r) => r.eligible).length;

  return (
    <div>
      <h1 className="page-title">Rankings</h1>

      <div className="kpi-grid">
        <KPI label="Symbols Ranked" value={String(rankings.length)} />
        <KPI label="Eligible" value={String(eligible)} />
        <KPI label="Avg Score" value={(avgScore * 100).toFixed(0)} sub="/ 100" />
        <KPI label="Trade Plans" value={String(plans.length)} />
      </div>

      <div className="grid-2" style={{ marginBottom: 'var(--gap)' }}>
        <Card>
          <CardHead title="Top Bullish" subtitle="eligible, score ≥ 0.55" />
          <CardBody flush>
            <DataTable
              data={bullish as unknown as Record<string, unknown>[]}
              columns={RANKING_COLS.slice(0, 3) as unknown as Column<Record<string, unknown>>[]}
              emptyMessage="No bullish candidates"
            />
          </CardBody>
        </Card>
        <Card>
          <CardHead title="Top Bearish" subtitle="eligible, score ≤ 0.45" />
          <CardBody flush>
            <DataTable
              data={bearish as unknown as Record<string, unknown>[]}
              columns={RANKING_COLS.slice(0, 3) as unknown as Column<Record<string, unknown>>[]}
              emptyMessage="No bearish candidates"
            />
          </CardBody>
        </Card>
      </div>

      <Card style={{ marginBottom: 'var(--gap)' }}>
        <CardHead
          title="Full Rankings"
          subtitle={`${displayed.length} shown`}
          right={
            <div className="seg-ctrl">
              {(['all', 'bullish', 'bearish'] as const).map((t) => (
                <button key={t} className={`seg-ctrl-item${activeTab === t ? ' active' : ''}`} onClick={() => setActiveTab(t)}>
                  {t.charAt(0).toUpperCase() + t.slice(1)}
                </button>
              ))}
            </div>
          }
        />
        <CardBody flush>
          <DataTable
            data={displayed as unknown as Record<string, unknown>[]}
            columns={RANKING_COLS as unknown as Column<Record<string, unknown>>[]}
            emptyMessage="No rankings"
          />
        </CardBody>
      </Card>

      <Card>
        <CardHead title="Trade Plans" subtitle={`${plans.length} recent`} />
        <CardBody flush>
          <DataTable
            data={plans as unknown as Record<string, unknown>[]}
            columns={PLAN_COLS as unknown as Column<Record<string, unknown>>[]}
            emptyMessage="No trade plans"
          />
        </CardBody>
      </Card>
    </div>
  );
}
