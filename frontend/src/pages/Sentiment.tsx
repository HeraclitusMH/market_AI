import { useState } from 'react';
import { useQuery, useMutation } from '@tanstack/react-query';
import { RefreshCw } from 'lucide-react';
import { api } from '@/lib/api';
import { fmtTs, fmtDateShort } from '@/lib/formatters';
import { KPI } from '@/components/KPI';
import { Card, CardHead, CardBody } from '@/components/Card';
import { LineChart } from '@/components/LineChart';
import { DataTable, type Column } from '@/components/DataTable';
import { Badge } from '@/components/Badge';
import { Button } from '@/components/Button';
import { queryClient } from '@/lib/queryClient';
import type { SentimentRow } from '@/types/api';

function scoreColor(s: number): string {
  if (s > 0.15) return 'var(--pos)';
  if (s < -0.15) return 'var(--neg)';
  return 'var(--warn)';
}

function scoreBadge(s: number) {
  return (
    <span className="mono" style={{ color: scoreColor(s), fontSize: 12.5 }}>
      {s >= 0 ? '+' : ''}{s.toFixed(3)}
    </span>
  );
}

const SECTOR_COLS: Column<SentimentRow>[] = [
  { key: 'key', header: 'Sector', render: (r) => <strong style={{ color: 'var(--ink-1)' }}>{r.key}</strong> },
  { key: 'score', header: 'Score', numeric: true, render: (r) => scoreBadge(r.score) },
  { key: 'summary', header: 'Summary', sortable: false, render: (r) => <span style={{ color: 'var(--ink-3)', fontSize: 12 }}>{r.summary?.slice(0, 100)}{(r.summary?.length ?? 0) > 100 ? '…' : ''}</span> },
];

const TICKER_COLS: Column<SentimentRow>[] = [
  { key: 'key', header: 'Ticker', render: (r) => <strong style={{ color: 'var(--ink-1)' }}>{r.key}</strong> },
  { key: 'score', header: 'Score', numeric: true, render: (r) => scoreBadge(r.score) },
  { key: 'timestamp', header: 'Updated', render: (r) => <span className="mono" style={{ fontSize: 11.5 }}>{fmtTs(r.timestamp)}</span> },
];

export function Sentiment() {
  const { data, isLoading, dataUpdatedAt } = useQuery({ queryKey: ['sentiment'], queryFn: api.getSentiment, refetchInterval: 30_000 });
  const [expanded, setExpanded] = useState<number | null>(null);

  const refresh = useMutation({
    mutationFn: api.refreshSentiment,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['sentiment'] }),
  });

  if (isLoading) return <div className="loading-state">Loading sentiment…</div>;
  if (!data) return null;

  const budget = data.budget;
  const budgetPct = budget.monthly_cap_eur > 0
    ? (budget.month_to_date_eur / budget.monthly_cap_eur) * 100
    : 0;

  const historyChartData = data.history.map((h) => ({
    x: fmtDateShort(h.timestamp),
    score: h.score,
  }));

  const mktScore = data.market?.score ?? 0;

  return (
    <div>
      <h1 className="page-title">Sentiment</h1>

      <div className="kpi-grid">
        <KPI
          label="Market Score"
          value={`${mktScore >= 0 ? '+' : ''}${mktScore.toFixed(3)}`}
          color={mktScore > 0.1 ? 'pos' : mktScore < -0.1 ? 'neg' : 'neutral'}
        />
        <KPI label="Provider" value={data.provider} />
        <KPI label="LLM Budget Used" value={`€${budget.month_to_date_eur.toFixed(2)}`} sub={`/ €${budget.monthly_cap_eur.toFixed(2)} cap`} />
        <KPI label="Budget Remaining" value={`€${budget.remaining_month_eur.toFixed(2)}`} color={budget.budget_stopped ? 'neg' : 'neutral'} />
      </div>

      <div className="grid-2">
        <Card>
          <CardHead
            title="Market Sentiment Trend"
            subtitle="60h history"
            right={
              <Button
                variant="ghost"
                size="sm"
                loading={refresh.isPending}
                onClick={() => refresh.mutate()}
                icon={<RefreshCw size={12} />}
              >
                Refresh
              </Button>
            }
          />
          <CardBody>
            <LineChart
              data={historyChartData}
              lines={[{ key: 'score', name: 'Score', color: 'var(--accent)' }]}
              height={200}
            />
          </CardBody>
        </Card>

        <Card>
          <CardHead title="LLM Budget" subtitle={budget.provider} />
          <CardBody>
            <div style={{ marginBottom: 10 }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12, color: 'var(--ink-4)', marginBottom: 6 }}>
                <span>Month to date</span>
                <span className="mono">€{budget.month_to_date_eur.toFixed(3)} / €{budget.monthly_cap_eur.toFixed(2)}</span>
              </div>
              <div className="budget-bar-track">
                <div className="budget-bar-fill" style={{ width: `${Math.min(budgetPct, 100)}%`, background: budgetPct > 80 ? 'var(--neg)' : budgetPct > 60 ? 'var(--warn)' : 'var(--accent)' }} />
              </div>
            </div>
            <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12, color: 'var(--ink-4)', marginTop: 8 }}>
              <span>Today</span>
              <span className="mono">€{budget.today_eur.toFixed(3)}</span>
            </div>
            <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12, color: 'var(--ink-4)', marginTop: 4 }}>
              <span>Hard stop</span>
              <Badge variant={budget.budget_stopped ? 'neg' : 'neutral'} dot>{budget.budget_stopped ? 'Active' : 'OK'}</Badge>
            </div>
            {budget.reason && (
              <div style={{ marginTop: 10, fontSize: 11.5, color: 'var(--warn)', padding: '6px 8px', background: 'var(--warn-bg)', borderRadius: 5 }}>
                {budget.reason}
              </div>
            )}
          </CardBody>
        </Card>
      </div>

      <div className="grid-2">
        <Card>
          <CardHead title="Sectors" subtitle={`${data.sectors.length} tracked`} />
          <CardBody flush>
            <DataTable
              data={data.sectors as unknown as Record<string, unknown>[]}
              columns={SECTOR_COLS as unknown as Column<Record<string, unknown>>[]}
              emptyMessage="No sector data"
            />
          </CardBody>
        </Card>

        <Card>
          <CardHead title="Top Tickers" subtitle={`${data.tickers.length} shown`} />
          <CardBody flush>
            <DataTable
              data={data.tickers as unknown as Record<string, unknown>[]}
              columns={TICKER_COLS as unknown as Column<Record<string, unknown>>[]}
              emptyMessage="No ticker data"
            />
          </CardBody>
        </Card>
      </div>

      {data.headlines.length > 0 && (
        <Card>
          <CardHead title="Headlines Analysed" subtitle={`${data.headlines.length} items`} />
          <CardBody>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 0 }}>
              {data.headlines.map((h, i) => (
                <div
                  key={i}
                  style={{
                    display: 'flex', justifyContent: 'space-between', alignItems: 'baseline',
                    gap: 12, padding: '7px 0', borderBottom: i < data.headlines.length - 1 ? '1px solid var(--line-soft)' : 'none',
                  }}
                >
                  <button
                    style={{ background: 'none', border: 'none', cursor: 'pointer', textAlign: 'left', fontSize: 12.5, color: expanded === i ? 'var(--ink-1)' : 'var(--ink-3)', padding: 0 }}
                    onClick={() => setExpanded(expanded === i ? null : i)}
                  >
                    {h.title}
                  </button>
                  <span className="mono" style={{ color: scoreColor(h.score), fontSize: 12, flexShrink: 0 }}>
                    {h.score >= 0 ? '+' : ''}{h.score.toFixed(3)}
                  </span>
                </div>
              ))}
            </div>
          </CardBody>
        </Card>
      )}

      {dataUpdatedAt > 0 && (
        <p style={{ fontSize: 11, color: 'var(--ink-5)', marginTop: 8 }}>
          Last updated: {new Date(dataUpdatedAt).toLocaleTimeString()}
        </p>
      )}
    </div>
  );
}
