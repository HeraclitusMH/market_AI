import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '@/lib/api';
import { fmtTs } from '@/lib/formatters';
import { KPI } from '@/components/KPI';
import { Card, CardHead, CardBody } from '@/components/Card';
import { DataTable, type Column } from '@/components/DataTable';
import { ScoreBar } from '@/components/ScoreBar';
import { Badge } from '@/components/Badge';
import { symbolCell } from '@/lib/cells';
import type { RankingRow, PlanRow, RankingComponents, RankingFactor, Composite7Factor } from '@/types/api';

const COMPOSITE_FACTOR_KEYS: [string, string][] = [
  ['quality', 'Quality'],
  ['value', 'Value'],
  ['momentum', 'Momentum'],
  ['growth', 'Growth'],
  ['sentiment', 'Sentiment'],
  ['technical', 'Technical'],
  ['risk', 'Risk Penalty'],
];

function isFactor(value: unknown): value is RankingFactor {
  return value !== null && typeof value === 'object' && (
    'value_0_1' in value || 'status' in value || 'eligible' in value || 'metrics' in value
  );
}

function scoreColor(value: number | null): string {
  if (value == null) return 'var(--ink-3)';
  const v = Math.min(Math.max(value, 0), 1);
  if (v >= 0.7) return 'var(--pos)';
  if (v <= 0.35) return 'var(--neg)';
  return 'var(--warn)';
}

function isComposite7Factor(value: unknown): value is Composite7Factor {
  return value !== null && typeof value === 'object' && 'composite_score' in value && 'factors' in value;
}

function composite7Factor(components: RankingComponents): Composite7Factor | null {
  return isComposite7Factor(components.composite_7factor) ? components.composite_7factor : null;
}

function ScoreFormula({ components, total }: { components: RankingComponents; total: number }) {
  const composite = composite7Factor(components);
  if (!composite) return null;

  const terms = COMPOSITE_FACTOR_KEYS
    .map(([key, label]) => {
      const factor = composite.factors[key];
      if (!factor) return null;
      return { key, label, factor, isRisk: key === 'risk' };
    })
    .filter((t): t is NonNullable<typeof t> => t !== null);

  return (
    <div style={{ marginTop: 10 }}>
      <span style={{ fontSize: 10.5, color: 'var(--ink-4)', marginRight: 6 }}>Formula</span>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: '3px 8px', alignItems: 'center', marginTop: 3 }}>
        {terms.map(({ key, label, factor, isRisk }) => (
          <span
            key={key}
            className="mono"
            style={{ fontSize: 10.5, whiteSpace: 'nowrap', color: isRisk ? 'var(--neg)' : 'var(--ink-2)' }}
          >
            {isRisk ? '−' : '+'} {label} {Math.round(factor.score)} × {(factor.weight * 100).toFixed(0)}%
          </span>
        ))}
        <span className="mono" style={{ fontSize: 10.5, whiteSpace: 'nowrap', color: 'var(--ink-1)', fontWeight: 600 }}>
          = {Math.round(total * 100)}
        </span>
        <span style={{ fontSize: 10.5, whiteSpace: 'nowrap', color: 'var(--ink-4)' }}>
          {composite.regime} · {(composite.confidence * 100).toFixed(0)}% conf
        </span>
      </div>
    </div>
  );
}

function LiquidityGate({ factor }: { factor: unknown }) {
  if (!isFactor(factor)) return null;
  const passed = factor.eligible !== false;
  const metrics = factor.metrics ?? {};
  const adv = typeof metrics.adv_dollar_20d === 'number' ? `$${Math.round(metrics.adv_dollar_20d).toLocaleString()}` : null;
  const price = typeof metrics.last_price === 'number' ? `$${metrics.last_price.toFixed(2)}` : null;
  const detail = [price && `Price ${price}`, adv && `ADV ${adv}`, ...(factor.reasons ?? [])].filter(Boolean).join(' - ');

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

function FactorBreakdown({
  components,
  total,
  symbol,
}: {
  components: RankingComponents;
  total: number;
  symbol: string;
}) {
  const queryClient = useQueryClient();
  const refreshOne = useMutation({
    mutationFn: () => api.refreshFundamentals(symbol),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['rankings'] }),
  });
  const composite = composite7Factor(components);

  return (
    <div style={{ padding: '8px 12px 12px' }}>
      {!composite && (
        <div style={{ fontSize: 11.5, color: 'var(--warn)', marginBottom: 8 }}>
          Missing 7-factor composite payload for this persisted ranking row.
        </div>
      )}
      {COMPOSITE_FACTOR_KEYS.map(([k, label]) => {
        const factor = composite?.factors[k];
        const value = factor ? factor.score / 100 : null;
        const displayPct = value == null ? 0 : Math.round(Math.min(Math.max(value, 0), 1) * 100);
        return (
          <div key={k} className="factor-bar-row">
            <span className="factor-bar-name">{label}</span>
            <div className="factor-bar-track">
              <div className="factor-bar-fill" style={{ width: `${displayPct}%`, background: scoreColor(value) }} />
            </div>
            <span className="factor-bar-val" title={`weight ${(factor?.weight ?? 0) * 100}%`}>
              {factor ? `${factor.contribution >= 0 ? '+' : ''}${factor.contribution.toFixed(2)}` : '--'}
            </span>
          </div>
        );
      })}
      <button
        type="button"
        onClick={(e) => { e.preventDefault(); e.stopPropagation(); refreshOne.mutate(); }}
        disabled={refreshOne.isPending}
        style={{
          background: 'transparent',
          border: '1px solid var(--bg-3)',
          color: 'var(--accent)',
          fontSize: 11,
          padding: '2px 8px',
          borderRadius: 4,
          cursor: refreshOne.isPending ? 'wait' : 'pointer',
          marginTop: 6,
        }}
        title={`Recalculate fundamentals for ${symbol}`}
      >
        {refreshOne.isPending ? 'Refreshing...' : 'Refresh Fundamentals'}
      </button>
      <LiquidityGate factor={components.liquidity} />
      <ScoreFormula components={components} total={total} />
    </div>
  );
}

const RANKING_COLS: Column<RankingRow>[] = [
  { key: 'symbol', header: 'Company', render: (r) => symbolCell(r) },
  { key: 'score_total', header: 'Score', numeric: true, render: (r) => <ScoreBar value={r.score_total} /> },
  {
    key: 'eligible', header: 'Eligible', sortable: false,
    render: (r) => <Badge variant={r.eligible ? 'pos' : 'neutral'} dot>{r.eligible ? 'Yes' : 'No'}</Badge>,
  },
];

const PLAN_COLS: Column<PlanRow>[] = [
  { key: 'ts', header: 'Time', render: (r) => <span className="mono" style={{ fontSize: 11.5 }}>{fmtTs(r.ts)}</span> },
  { key: 'symbol', header: 'Company', render: (r) => symbolCell(r) },
  { key: 'bias', header: 'Bias', render: (r) => <Badge variant={r.bias === 'bullish' ? 'pos' : 'neg'}>{r.bias}</Badge> },
  { key: 'strategy', header: 'Strategy' },
  { key: 'expiry', header: 'Expiry', render: (r) => r.expiry ?? '-' },
  { key: 'dte', header: 'DTE', numeric: true, render: (r) => r.dte != null ? String(r.dte) : '-' },
  { key: 'status', header: 'Status', render: (r) => <Badge variant={r.status === 'pending' ? 'warn' : r.status === 'submitted' ? 'info' : 'neutral'} dot>{r.status}</Badge> },
];

export function Rankings() {
  const queryClient = useQueryClient();
  const { data: rankings = [], isLoading: rankLoading } = useQuery({ queryKey: ['rankings'], queryFn: () => api.getRankings(100), refetchInterval: 30_000 });
  const { data: plans = [], isLoading: planLoading } = useQuery({ queryKey: ['tradePlans'], queryFn: () => api.getTradePlans(50), refetchInterval: 30_000 });

  const refreshAll = useMutation({
    mutationFn: () => api.refreshFundamentals(),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['rankings'] }),
  });

  const [activeTab, setActiveTab] = useState<'all' | 'bullish' | 'bearish'>('all');

  if (rankLoading || planLoading) return <div className="loading-state">Loading rankings...</div>;

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
          <CardHead title="Top Bullish" subtitle="eligible, score >= 0.55" />
          <CardBody flush>
            <DataTable
              data={bullish as unknown as Record<string, unknown>[]}
              columns={RANKING_COLS.slice(0, 3) as unknown as Column<Record<string, unknown>>[]}
              emptyMessage="No bullish candidates"
            />
          </CardBody>
        </Card>
        <Card>
          <CardHead title="Top Bearish" subtitle="eligible, score <= 0.45" />
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
          subtitle={
            refreshAll.isSuccess
              ? `${displayed.length} shown - refreshed ${refreshAll.data.refreshed} (missing ${refreshAll.data.missing}) in ${refreshAll.data.duration_s}s`
              : `${displayed.length} shown`
          }
          right={
            <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
              <button
                type="button"
                onClick={() => refreshAll.mutate()}
                disabled={refreshAll.isPending}
                style={{
                  background: 'transparent',
                  border: '1px solid var(--bg-3)',
                  color: 'var(--accent)',
                  fontSize: 12,
                  padding: '4px 10px',
                  borderRadius: 4,
                  cursor: refreshAll.isPending ? 'wait' : 'pointer',
                }}
                title="Force-refresh yfinance fundamentals for every symbol in the universe"
              >
                {refreshAll.isPending ? 'Refreshing...' : 'Refresh Fundamentals'}
              </button>
              <div className="seg-ctrl">
                {(['all', 'bullish', 'bearish'] as const).map((t) => (
                  <button key={t} className={`seg-ctrl-item${activeTab === t ? ' active' : ''}`} onClick={() => setActiveTab(t)}>
                    {t.charAt(0).toUpperCase() + t.slice(1)}
                  </button>
                ))}
              </div>
            </div>
          }
        />
        <CardBody flush>
          <DataTable
            data={displayed as unknown as Record<string, unknown>[]}
            columns={RANKING_COLS as unknown as Column<Record<string, unknown>>[]}
            expandRow={(r) => {
              const row = r as unknown as RankingRow;
              return <FactorBreakdown components={row.components} total={row.score_total} symbol={row.symbol} />;
            }}
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
