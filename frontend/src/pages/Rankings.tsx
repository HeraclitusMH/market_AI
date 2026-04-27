import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { api } from '@/lib/api';
import { fmtTs } from '@/lib/formatters';
import { KPI } from '@/components/KPI';
import { Card, CardHead, CardBody } from '@/components/Card';
import { DataTable, type Column } from '@/components/DataTable';
import { ScoreBar, FactorBar } from '@/components/ScoreBar';
import { Badge } from '@/components/Badge';
import type { RankingRow, PlanRow, RankingComponents, RankingFactor } from '@/types/api';

const SCORE_FACTOR_KEYS: [string, string][] = [
  ['sentiment', 'Sentiment'],
  ['momentum_trend', 'Momentum'],
  ['risk', 'Risk'],
  ['fundamentals', 'Fundamentals'],
];

function isFactor(value: unknown): value is RankingFactor {
  return value !== null && typeof value === 'object' && (
    'value_0_1' in value || 'status' in value || 'eligible' in value || 'metrics' in value
  );
}

function factorValue(factor: unknown): number | null {
  if (!isFactor(factor)) return null;
  const value = factor?.value_0_1;
  return Number.isFinite(value) ? value as number : null;
}

function pct(value: number | null): string {
  return value == null ? '--' : String(Math.round(Math.min(Math.max(value, 0), 1) * 100));
}

function ScoreFormula({ components, total }: { components: RankingComponents; total: number }) {
  const weights = components.weights_used ?? {};
  const terms = SCORE_FACTOR_KEYS
    .map(([key, label]) => {
      const value = factorValue(components[key]);
      const weight = weights[key];
      if (value == null || !Number.isFinite(weight) || weight <= 0) return null;
      return `${label} ${pct(value)} x ${(weight * 100).toFixed(1)}%`;
    })
    .filter(Boolean);

  if (!terms.length) return null;

  return (
    <div style={{ marginTop: 8, fontSize: 11.5, color: 'var(--ink-2)' }}>
      <span style={{ color: 'var(--ink-3)' }}>Formula: </span>
      <span className="mono">{terms.join(' + ')} = {pct(total)}</span>
    </div>
  );
}

function LiquidityGate({ factor }: { factor: unknown }) {
  if (!isFactor(factor)) return null;
  const passed = factor.eligible !== false;
  const metrics = factor.metrics ?? {};
  const adv = typeof metrics.adv_dollar_20d === 'number' ? `$${Math.round(metrics.adv_dollar_20d).toLocaleString()}` : null;
  const price = typeof metrics.last_price === 'number' ? `$${metrics.last_price.toFixed(2)}` : null;
  const detail = [price && `Price ${price}`, adv && `ADV ${adv}`, ...(factor.reasons ?? [])].filter(Boolean).join(' · ');

  return (
    <div className="factor-bar-row">
      <span className="factor-bar-name">Liquidity Gate</span>
      <div style={{ flex: 1, minWidth: 0, color: 'var(--ink-2)', fontSize: 11.5, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
        {detail || factor.status || 'No liquidity data'}
      </div>
      <span className="factor-bar-val" title={factor.status}>
        {passed ? 'Pass' : 'Fail'}
      </span>
    </div>
  );
}

function FactorBreakdown({ components, total }: { components: RankingComponents; total: number }) {
  return (
    <div style={{ padding: '8px 12px 12px' }}>
      {SCORE_FACTOR_KEYS.map(([k, label]) => {
        const factor = components[k];
        if (!isFactor(factor)) return null;
        return <FactorBar key={k} name={label} value={factorValue(factor)} status={factor.status} />;
      })}
      <LiquidityGate factor={components.liquidity} />
      <ScoreFormula components={components} total={total} />
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
        <FactorBreakdown components={r.components} total={r.score_total} />
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
