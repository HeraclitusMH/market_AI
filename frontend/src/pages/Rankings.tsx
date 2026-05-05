import React, { useState, useEffect } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { RefreshCw } from 'lucide-react';
import { api } from '@/lib/api';
import { fmtTs } from '@/lib/formatters';
import { KPI } from '@/components/KPI';
import { Button } from '@/components/Button';
import { Card, CardHead, CardBody } from '@/components/Card';
import { DataTable, type Column } from '@/components/DataTable';
import { ScoreBar } from '@/components/ScoreBar';
import { Badge } from '@/components/Badge';
import { regimeLabel } from '@/components/RegimeSummaryCard';
import { symbolCell } from '@/lib/cells';
import type { RankingRow, PlanRow, RankingComponents, RankingFactor, Composite6Factor } from '@/types/api';

const COMPOSITE_FACTOR_KEYS: [string, string][] = [
  ['quality', 'Quality'],
  ['momentum', 'Momentum'],
  ['growth', 'Growth'],
  ['sentiment', 'Sentiment'],
  ['technical', 'Technical'],
  ['risk_penalty', 'Risk Penalty'],
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

function isComposite6Factor(value: unknown): value is Composite6Factor {
  return value !== null && typeof value === 'object' && 'composite_score' in value && 'factors' in value;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object';
}

function composite6Factor(components: RankingComponents): Composite6Factor | null {
  return isComposite6Factor(components.composite_6factor) ? components.composite_6factor : null;
}

function factorIsMissing(key: string, components: RankingComponents): boolean {
  if (key !== 'sentiment') return false;
  const sentiment = components.sentiment;
  return isFactor(sentiment) && sentiment.status === 'missing';
}

function sentimentComponents(components: RankingComponents) {
  const sentiment = components.sentiment;
  if (!isFactor(sentiment) || !isRecord(sentiment.components)) return [];

  return (['market', 'sector', 'ticker'] as const).map((key) => {
    const item = sentiment.components?.[key];
    const row = isRecord(item) ? item : {};
    const raw = typeof row.raw === 'number' ? row.raw : null;
    const status = typeof row.status === 'string' ? row.status : 'missing';
    const weight = typeof row.weight === 'number' ? row.weight : null;
    return { key, raw, status, weight };
  });
}

function SentimentSourceBreakdown({ components }: { components: RankingComponents }) {
  const rows = sentimentComponents(components);
  if (!rows.length) return null;

  return (
    <div style={{ marginTop: 10, display: 'flex', flexWrap: 'wrap', gap: 8, alignItems: 'center' }}>
      <span style={{ fontSize: 10.5, color: 'var(--ink-4)', marginRight: 2 }}>Sentiment inputs</span>
      {rows.map((row) => (
        <span
          key={row.key}
          className="mono"
          title={row.weight == null ? undefined : `weight ${(row.weight * 100).toFixed(0)}%`}
          style={{ fontSize: 10.5, color: row.status === 'ok' ? 'var(--ink-2)' : 'var(--ink-4)', whiteSpace: 'nowrap' }}
        >
          {row.key}: {row.status === 'ok' && row.raw != null ? row.raw.toFixed(2) : row.status}
        </span>
      ))}
    </div>
  );
}

function ScoreFormula({ components, total }: { components: RankingComponents; total: number }) {
  const composite = composite6Factor(components);
  if (!composite) return null;

  const terms = COMPOSITE_FACTOR_KEYS
    .map(([key, label]) => {
      const factor = composite.factors[key];
      if (!factor) return null;
      return { key, label, factor, isRisk: key === 'risk_penalty' };
    })
    .filter((t): t is NonNullable<typeof t> => t !== null)
    .filter((t) => !factorIsMissing(t.key, components));

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
            {isRisk ? '-' : '+'} {label} {(factor.score * 100).toFixed(0)} x {(factor.weight * 100).toFixed(0)}%
          </span>
        ))}
        <span className="mono" style={{ fontSize: 10.5, whiteSpace: 'nowrap', color: 'var(--ink-1)', fontWeight: 600 }}>
          = {Math.round(total * 100)}
        </span>
        <span style={{ fontSize: 10.5, whiteSpace: 'nowrap', color: 'var(--ink-4)' }}>
          {composite.regime} · {composite.weight_profile} · {(composite.confidence * 100).toFixed(0)}% conf
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
}: {
  components: RankingComponents;
  total: number;
}) {
  const composite = composite6Factor(components);

  return (
    <div style={{ padding: '8px 12px 12px' }}>
      {!composite && (
        <div style={{ fontSize: 11.5, color: 'var(--warn)', marginBottom: 8 }}>
          Missing 6-factor composite payload for this persisted ranking row.
        </div>
      )}
      {COMPOSITE_FACTOR_KEYS.map(([k, label]) => {
        const factor = composite?.factors[k];
        const missing = factorIsMissing(k, components);
        const value = factor && !missing ? factor.score : null;
        const displayPct = value == null ? 0 : Math.round(Math.min(Math.max(value, 0), 1) * 100);
        return (
          <div key={k} className="factor-bar-row">
            <span className="factor-bar-name">{label}</span>
            <div className="factor-bar-track" style={{ '--score-pct': displayPct } as React.CSSProperties}>
              <div className="factor-bar-fill" style={{ width: `${displayPct}%`, background: scoreColor(value) }} />
            </div>
            <span className="factor-bar-val" title={`weight ${(factor?.weight ?? 0) * 100}%`}>
              {missing ? 'missing' : factor ? `${factor.contribution >= 0 ? '+' : ''}${factor.contribution.toFixed(2)}` : '--'}
            </span>
          </div>
        );
      })}
      <LiquidityGate factor={components.liquidity} />
      <SentimentSourceBreakdown components={components} />
      <ScoreFormula components={components} total={total} />
    </div>
  );
}

const RANKING_COLS: Column<RankingRow>[] = [
  { key: 'symbol', header: 'Company', render: (r) => symbolCell(r) },
  { key: 'score_total', header: 'Score', numeric: true, render: (r) => <ScoreBar value={r.score_total} /> },
  {
    key: 'eligible', header: 'Eligible', center: true,
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

const PAGE_SIZE = 150;

function compareBySortKey<T extends Record<string, unknown>>(a: T, b: T, key: string, dir: 'asc' | 'desc') {
  const av = a[key];
  const bv = b[key];
  let result: number;
  if (typeof av === 'number' && typeof bv === 'number') {
    result = av - bv;
  } else if (typeof av === 'boolean' && typeof bv === 'boolean') {
    result = Number(av) - Number(bv);
  } else {
    result = String(av ?? '').localeCompare(String(bv ?? ''));
  }
  return dir === 'asc' ? result : -result;
}

export function Rankings() {
  const queryClient = useQueryClient();
  const { data: rankings = [], isLoading: rankLoading } = useQuery({ queryKey: ['rankings'], queryFn: () => api.getRankings(2000), refetchInterval: 30_000 });
  const { data: plans = [], isLoading: planLoading } = useQuery({ queryKey: ['tradePlans'], queryFn: () => api.getTradePlans(50), refetchInterval: 30_000 });
  const { data: currentRegime } = useQuery({ queryKey: ['regimeCurrent'], queryFn: api.getRegimeCurrent, refetchInterval: 20_000 });

  const [activeTab, setActiveTab] = useState<'all' | 'bullish' | 'bearish'>('all');
  const [page, setPage] = useState(0);
  const [refreshMessage, setRefreshMessage] = useState<string>('');
  const [rankingSortKey, setRankingSortKey] = useState<string | null>(null);
  const [rankingSortDir, setRankingSortDir] = useState<'asc' | 'desc'>('desc');

  useEffect(() => { setPage(0); }, [activeTab]);
  function handleRankingSort(key: string, dir: 'asc' | 'desc') {
    setRankingSortKey(key);
    setRankingSortDir(dir);
    setPage(0);
  }

  const refreshRankings = useMutation({
    mutationFn: api.refreshRankings,
    onSuccess: async (result) => {
      const ts = result.latest_ts ? fmtTs(result.latest_ts) : 'latest batch';
      setRefreshMessage(`Refreshed ${result.ranked} symbols at ${ts}`);
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['rankings'] }),
        queryClient.invalidateQueries({ queryKey: ['tradePlans'] }),
      ]);
    },
    onError: (error) => {
      setRefreshMessage(error instanceof Error ? error.message : 'Refresh failed');
    },
  });

  if (rankLoading || planLoading) return <div className="loading-state">Loading rankings...</div>;

  const bullish = rankings.filter((r) => r.eligible && r.score_total >= 0.48).slice(0, 10);
  const bearish = rankings.filter((r) => r.eligible && r.score_total <= 0.35).sort((a, b) => a.score_total - b.score_total).slice(0, 10);
  const displayed = activeTab === 'bullish' ? bullish : activeTab === 'bearish' ? bearish : rankings;
  const sortedDisplayed = rankingSortKey
    ? [...displayed].sort((a, b) => compareBySortKey(a as unknown as Record<string, unknown>, b as unknown as Record<string, unknown>, rankingSortKey, rankingSortDir))
    : displayed;
  const pageCount = Math.ceil(sortedDisplayed.length / PAGE_SIZE);
  const paginated = sortedDisplayed.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE);

  const avgScore = rankings.length ? rankings.reduce((s, r) => s + r.score_total, 0) / rankings.length : 0;
  const eligible = rankings.filter((r) => r.eligible).length;

  return (
    <div>
      <h1 className="page-title">Rankings</h1>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, alignItems: 'center', marginBottom: 'var(--gap)' }}>
        <div style={{ color: refreshRankings.isError ? 'var(--neg)' : 'var(--ink-4)', fontSize: 12 }}>
          {refreshRankings.isPending ? 'Refreshing rankings...' : refreshMessage}
        </div>
        <Button
          size="sm"
          variant="primary"
          icon={<RefreshCw size={14} />}
          loading={refreshRankings.isPending}
          onClick={() => refreshRankings.mutate()}
        >
          Refresh rankings
        </Button>
      </div>

      <div className="kpi-grid">
        <KPI label="Symbols Ranked" value={String(rankings.length)} />
        <KPI label="Eligible" value={String(eligible)} />
        <KPI label="Avg Score" value={(avgScore * 100).toFixed(0)} sub="/ 100" />
        <KPI label="Regime" value={regimeLabel(currentRegime?.level)} sub={`${plans.length} trade plans`} />
      </div>

      <div className="grid-2" style={{ marginBottom: 'var(--gap)' }}>
        <Card>
          <CardHead title="Top Bullish" subtitle="eligible, score >= 0.48" />
          <CardBody flush>
            <DataTable
              data={bullish as unknown as Record<string, unknown>[]}
              columns={RANKING_COLS.slice(0, 3) as unknown as Column<Record<string, unknown>>[]}
              emptyMessage="No bullish candidates"
            />
          </CardBody>
        </Card>
        <Card>
          <CardHead title="Top Bearish" subtitle="eligible, score <= 0.35" />
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
          subtitle={`${displayed.length} total`}
          right={
            <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
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
            data={paginated as unknown as Record<string, unknown>[]}
            columns={RANKING_COLS as unknown as Column<Record<string, unknown>>[]}
            sortKey={rankingSortKey}
            sortDir={rankingSortDir}
            onSortChange={handleRankingSort}
            manualSort
            expandRow={(r) => {
              const row = r as unknown as RankingRow;
              return <FactorBreakdown components={row.components} total={row.score_total} />;
            }}
            emptyMessage="No rankings"
          />
          {pageCount > 1 && (
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '10px 16px', borderTop: '1px solid var(--line-soft)', fontSize: 12 }}>
              <button
                className="btn sm ghost"
                onClick={() => setPage((p) => Math.max(0, p - 1))}
                disabled={page === 0}
              >
                ← Prev
              </button>
              <span style={{ color: 'var(--ink-4)' }}>
                Page {page + 1} of {pageCount} · {displayed.length} symbols
              </span>
              <button
                className="btn sm ghost"
                onClick={() => setPage((p) => Math.min(pageCount - 1, p + 1))}
                disabled={page === pageCount - 1}
              >
                Next →
              </button>
            </div>
          )}
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
