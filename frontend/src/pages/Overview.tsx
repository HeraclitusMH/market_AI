import { useEffect, useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { api } from '@/lib/api';
import { fmtMoney, fmtSignMoney, fmtPct, fmtTs, fmtDateShort, colorForPnl } from '@/lib/formatters';
import { useBotStore } from '@/store/botStore';
import { KPI } from '@/components/KPI';
import { Card, CardHead, CardBody } from '@/components/Card';
import { LineChart } from '@/components/LineChart';
import { DataTable, type Column } from '@/components/DataTable';
import { Sparkline } from '@/components/Sparkline';
import { symbolCell } from '@/lib/cells';
import { SegmentedControl } from '@/components/SegmentedControl';
import { Badge } from '@/components/Badge';
import { RegimeSummaryCard } from '@/components/RegimeSummaryCard';
import type { Position, EventLog, EquitySnapshot } from '@/types/api';

const TIME_RANGES = [
  { value: '7D', label: '7D' },
  { value: '30D', label: '30D' },
  { value: '90D', label: '90D' },
];

function filterByDays(history: EquitySnapshot[], days: number): EquitySnapshot[] {
  const cutoff = Date.now() - days * 86_400_000;
  const filtered = history.filter((e) => new Date(e.timestamp).getTime() >= cutoff);
  return filtered.length ? filtered : history.slice(-Math.min(20, history.length));
}

const POSITION_COLS: Column<Position>[] = [
  { key: 'symbol', header: 'Company', render: (r) => symbolCell(r) },
  { key: 'instrument', header: 'Type', render: (r) => <Badge variant="info">{r.instrument}</Badge> },
  { key: 'quantity', header: 'Qty', numeric: true },
  { key: 'avg_cost', header: 'Avg Cost', numeric: true, render: (r) => fmtMoney(r.avg_cost as number) },
  { key: 'market_value', header: 'Market Val', numeric: true, render: (r) => fmtMoney(r.market_value as number) },
  {
    key: 'unrealized_pnl', header: 'Unreal P&L', numeric: true,
    render: (r) => (
      <span style={{ color: colorForPnl(r.unrealized_pnl as number) }}>
        {fmtSignMoney(r.unrealized_pnl as number)}
      </span>
    ),
  },
  {
    key: '_spark', header: '', sortable: false,
    render: (r) => {
      const v = r.unrealized_pnl as number;
      return <Sparkline data={[r.avg_cost as number, r.market_price as number]} color={v >= 0 ? 'var(--pos)' : 'var(--neg)'} />;
    },
  },
];

export function Overview() {
  const { data, isLoading } = useQuery({ queryKey: ['overview'], queryFn: api.getOverview, refetchInterval: 15_000 });
  const { data: regime } = useQuery({ queryKey: ['regimeCurrent'], queryFn: api.getRegimeCurrent, refetchInterval: 20_000 });
  const setBot = useBotStore((s) => s.setBot);
  const [range, setRange] = useState('30D');

  useEffect(() => { if (data?.bot) setBot(data.bot); }, [data?.bot, setBot]);

  const chartData = useMemo(() => {
    if (!data?.equity_history.length) return [];
    const days = range === '7D' ? 7 : range === '30D' ? 30 : 90;
    const filtered = filterByDays(data.equity_history, days);
    return filtered.map((e) => ({
      x: fmtDateShort(e.timestamp),
      equity: e.net_liquidation,
      drawdown: -e.drawdown_pct,
    }));
  }, [data?.equity_history, range]);

  if (isLoading) return <div className="loading-state">Loading overview…</div>;
  if (!data) return null;

  const eq = data.equity;
  const bot = data.bot;
  const budget = data.sentiment_llm_budget;
  const budgetPct = budget && budget.monthly_cap_eur > 0
    ? (budget.month_to_date_eur / budget.monthly_cap_eur) * 100
    : 0;

  return (
    <div>
      <h1 className="page-title">Overview</h1>

      <div className="kpi-grid">
        <KPI label="Net Liquidation" value={fmtMoney(eq?.net_liquidation ?? 0)} />
        <KPI
          label="Unrealized P&L"
          value={fmtSignMoney(eq?.unrealized_pnl ?? 0)}
          color={(eq?.unrealized_pnl ?? 0) >= 0 ? 'pos' : 'neg'}
        />
        <KPI
          label="Realized P&L"
          value={fmtSignMoney(eq?.realized_pnl ?? 0)}
          color={(eq?.realized_pnl ?? 0) >= 0 ? 'pos' : 'neg'}
        />
        <KPI
          label="Drawdown"
          value={fmtPct(eq?.drawdown_pct ?? 0)}
          color={(eq?.drawdown_pct ?? 0) > 5 ? 'neg' : 'neutral'}
        />
      </div>

      <div className="grid-full">
        <RegimeSummaryCard regime={regime} />
      </div>

      <div className="grid-2">
        <Card>
          <CardHead
            title="Equity Curve"
            right={<SegmentedControl options={TIME_RANGES} value={range} onChange={setRange} aria-label="Time range" />}
          />
          <CardBody>
            <LineChart
              data={chartData}
              lines={[
                { key: 'equity', name: 'Net Liq', color: 'var(--accent)' },
                { key: 'drawdown', name: 'Drawdown %', color: 'var(--neg)', yAxisId: 'right' },
              ]}
              height={240}
            />
          </CardBody>
        </Card>

        <Card>
          <CardHead title="Bot Status" />
          <CardBody>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
              {(
                [
                  ['Trading', !bot.paused && !bot.kill_switch],
                  ['Kill Switch', bot.kill_switch],
                  ['Options', bot.options_enabled],
                  ['Approve Mode', bot.approve_mode],
                ] as [string, boolean][]
              ).map(([label, val]) => (
                <div key={label} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', fontSize: 13 }}>
                  <span style={{ color: 'var(--ink-3)' }}>{label}</span>
                  <Badge variant={val ? (label === 'Kill Switch' ? 'neg' : 'pos') : 'neutral'} dot>
                    {val ? 'On' : 'Off'}
                  </Badge>
                </div>
              ))}
              {budget && (
                <div style={{ marginTop: 6 }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 11.5, color: 'var(--ink-4)', marginBottom: 4 }}>
                    <span>LLM Budget ({budget.provider})</span>
                    <span className="mono">€{budget.month_to_date_eur.toFixed(2)} / €{budget.monthly_cap_eur.toFixed(2)}</span>
                  </div>
                  <div className="budget-bar-track">
                    <div
                      className="budget-bar-fill"
                      style={{
                        width: `${Math.min(budgetPct, 100)}%`,
                        background: budgetPct > 80 ? 'var(--neg)' : budgetPct > 60 ? 'var(--warn)' : 'var(--accent)',
                      }}
                    />
                  </div>
                </div>
              )}
            </div>
          </CardBody>
        </Card>
      </div>

      <div className="grid-2">
        <Card>
          <CardHead title="Top Positions" subtitle={`${data.position_count} open`} />
          <CardBody flush>
            <DataTable
              data={data.positions as unknown as Record<string, unknown>[]}
              columns={POSITION_COLS as unknown as Column<Record<string, unknown>>[]}
              emptyMessage="No open positions"
              maxHeight={320}
            />
          </CardBody>
        </Card>

        <Card>
          <CardHead title="Recent Events" />
          <CardBody>
            {data.recent_events.length === 0 ? (
              <div className="empty-state">No events</div>
            ) : (
              data.recent_events.map((e: EventLog) => (
                <div key={e.id} className="event-item">
                  <div className={`event-dot ${e.level}`} aria-hidden="true" />
                  <div>
                    <div className="event-msg">{e.message}</div>
                    <div className="event-ts">{fmtTs(e.timestamp)} · {e.type}</div>
                  </div>
                </div>
              ))
            )}
          </CardBody>
        </Card>
      </div>
    </div>
  );
}
