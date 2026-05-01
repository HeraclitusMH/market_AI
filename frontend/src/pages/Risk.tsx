import { useEffect, useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';
import { api } from '@/lib/api';
import { fmtMoney, fmtPct, fmtDateShort } from '@/lib/formatters';
import { useBotStore } from '@/store/botStore';
import { KPI } from '@/components/KPI';
import { Card, CardHead, CardBody } from '@/components/Card';
import { LineChart } from '@/components/LineChart';
import { Donut } from '@/components/Donut';
import { RegimeSummaryCard } from '@/components/RegimeSummaryCard';

export function Risk() {
  const { data, isLoading } = useQuery({ queryKey: ['risk'], queryFn: api.getRisk, refetchInterval: 20_000 });
  const { data: regime } = useQuery({ queryKey: ['regimeCurrent'], queryFn: api.getRegimeCurrent, refetchInterval: 20_000 });
  const setBot = useBotStore((s) => s.setBot);

  useEffect(() => { if (data?.bot) setBot(data.bot); }, [data?.bot, setBot]);

  const chartData = useMemo(() => {
    if (!data?.history.length) return [];
    return data.history.map((e) => ({
      x: fmtDateShort(e.timestamp),
      drawdown: -e.drawdown_pct,
      equity: e.net_liquidation,
    }));
  }, [data?.history]);

  if (isLoading) return <div className="loading-state">Loading risk…</div>;
  if (!data) return null;

  const eq = data.current;
  const rc = data.risk_config;

  return (
    <div>
      <h1 className="page-title">Risk</h1>

      <div className="kpi-grid">
        <KPI
          label="Current Drawdown"
          value={fmtPct(eq?.drawdown_pct ?? 0)}
          sub={`Limit: ${fmtPct(rc.max_drawdown_pct)}`}
          color={(eq?.drawdown_pct ?? 0) > rc.max_drawdown_pct * 0.8 ? 'neg' : 'neutral'}
        />
        <KPI label="Max Risk / Trade" value={fmtPct(rc.max_risk_per_trade_pct)} />
        <KPI label="Cash" value={fmtMoney(eq?.cash ?? 0)} />
        <KPI label="Net Liquidation" value={fmtMoney(eq?.net_liquidation ?? 0)} />
      </div>

      <div className="grid-full">
        <RegimeSummaryCard regime={regime} />
      </div>

      <div className="grid-2">
        <Card>
          <CardHead title="Drawdown Utilisation" />
          <CardBody>
            <div style={{ display: 'flex', gap: 32, justifyContent: 'center', padding: '8px 0' }}>
              <div style={{ textAlign: 'center' }}>
                <Donut
                  value={eq?.drawdown_pct ?? 0}
                  max={rc.max_drawdown_pct}
                  color={(eq?.drawdown_pct ?? 0) / rc.max_drawdown_pct > 0.7 ? 'var(--neg)' : 'var(--accent)'}
                  label="Drawdown"
                  size={110}
                />
              </div>
              <div style={{ textAlign: 'center' }}>
                <Donut
                  value={data.positions_used}
                  max={data.positions_max}
                  color="var(--accent)"
                  label="Positions"
                  size={110}
                />
              </div>
            </div>
          </CardBody>
        </Card>

        <Card>
          <CardHead title="Limits" />
          <CardBody>
            {[
              ['Max Drawdown', fmtPct(rc.max_drawdown_pct)],
              ['Max Risk / Trade', fmtPct(rc.max_risk_per_trade_pct)],
              ['Max Positions', String(rc.max_positions)],
              ['Require Positive Cash', rc.require_positive_cash ? 'Yes' : 'No'],
              ['Kill Switch', data.bot.kill_switch ? 'ACTIVE' : 'Off'],
              ['Approve Mode', data.bot.approve_mode ? 'On' : 'Off'],
            ].map(([k, v]) => (
              <div key={k} className="config-row" style={{ marginBottom: 2 }}>
                <span className="config-key">{k}</span>
                <span className="config-val">{v}</span>
              </div>
            ))}
          </CardBody>
        </Card>
      </div>

      <Card>
        <CardHead title="90-day Equity + Drawdown" />
        <CardBody>
          <LineChart
            data={chartData}
            lines={[
              { key: 'equity', name: 'Net Liq', color: 'var(--accent)' },
              { key: 'drawdown', name: 'Drawdown %', color: 'var(--neg)', yAxisId: 'right' },
            ]}
            height={260}
          />
        </CardBody>
      </Card>
    </div>
  );
}
