import { useQuery } from '@tanstack/react-query';
import { api } from '@/lib/api';
import { Card, CardHead, CardBody } from '@/components/Card';
import { Badge } from '@/components/Badge';
import type { ParameterDoc } from '@/types/api';

function bandColor(label: string, inverted: boolean): string {
  const m = label.match(/^(\d+)\s*-\s*(\d+)$/);
  if (!m) return 'var(--ink-3)';
  const high = Number(m[2]);
  // colour by score band; for inverted (Risk Penalty) high score = bad
  const isGoodHigh = inverted ? high <= 39 : high >= 60;
  const isBadHigh = inverted ? high >= 60 : high <= 39;
  if (isGoodHigh) return 'var(--pos)';
  if (isBadHigh) return 'var(--neg)';
  return 'var(--warn)';
}

function ParameterCard({ param }: { param: ParameterDoc }) {
  const weightPct = Math.round(param.weight * 1000) / 10;
  const weightBarColor = param.key === 'risk_penalty' ? 'var(--neg)' : 'var(--accent)';

  return (
    <Card>
      <CardHead
        title={param.label}
        right={
          <div style={{ display: 'flex', alignItems: 'baseline', gap: 6 }}>
            <span style={{ fontSize: 22, fontWeight: 600, color: 'var(--ink-1)' }}>
              {weightPct}%
            </span>
            <span style={{ fontSize: 11, color: 'var(--ink-4)' }}>weight</span>
          </div>
        }
      />
      <CardBody>
        <div
          className="weight-bar"
          style={{
            height: 6,
            background: 'var(--bg-2)',
            borderRadius: 3,
            overflow: 'hidden',
            marginBottom: 14,
          }}
        >
          <div
            style={{
              width: `${Math.min(100, weightPct)}%`,
              height: '100%',
              background: weightBarColor,
              transition: 'width 0.2s',
            }}
          />
        </div>

        <div style={{ marginBottom: 12 }}>
          <div style={{ fontSize: 11, color: 'var(--ink-4)', textTransform: 'uppercase', letterSpacing: 0.4, marginBottom: 4 }}>
            What it measures
          </div>
          <p style={{ margin: 0, fontSize: 12.5, color: 'var(--ink-2)', lineHeight: 1.55 }}>
            {param.what}
          </p>
        </div>

        <div style={{ marginBottom: 12 }}>
          <div style={{ fontSize: 11, color: 'var(--ink-4)', textTransform: 'uppercase', letterSpacing: 0.4, marginBottom: 4 }}>
            How it&apos;s calculated
          </div>
          <p style={{ margin: 0, fontSize: 12.5, color: 'var(--ink-2)', lineHeight: 1.55 }}>
            {param.how}
          </p>
          <div style={{ fontSize: 11.5, color: 'var(--ink-4)', marginTop: 6 }}>
            <span>Source: </span>
            <span className="mono" style={{ color: 'var(--ink-2)' }}>{param.source}</span>
          </div>
          {param.subscores.length > 0 && (
            <ul style={{ margin: '6px 0 0 0', paddingLeft: 18, color: 'var(--ink-3)', fontSize: 11.5 }}>
              {param.subscores.map((s) => <li key={s}>{s}</li>)}
            </ul>
          )}
        </div>

        <div style={{ marginBottom: 12 }}>
          <div style={{ fontSize: 11, color: 'var(--ink-4)', textTransform: 'uppercase', letterSpacing: 0.4, marginBottom: 6 }}>
            Score range — {param.score_min}–{param.score_max}{param.inverted ? ' · inverted (lower = better)' : ''}
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
            {param.bands.map((b) => (
              <div key={b.range} style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 11.5 }}>
                <span
                  className="mono"
                  style={{
                    width: 56,
                    color: bandColor(b.range, param.inverted),
                    fontWeight: 600,
                  }}
                >
                  {b.range}
                </span>
                <span style={{ color: 'var(--ink-2)' }}>{b.label}</span>
              </div>
            ))}
          </div>
        </div>

        <div
          style={{
            marginTop: 12,
            paddingTop: 10,
            borderTop: '1px solid var(--line-soft)',
            fontSize: 11.5,
            color: 'var(--ink-3)',
          }}
        >
          <Badge variant="neutral">Refresh via: {param.refresh_via}</Badge>
          <div style={{ marginTop: 6 }}>{param.refresh_note}</div>
        </div>
      </CardBody>
    </Card>
  );
}

export function DocsTab() {
  const { data, isLoading, error } = useQuery({
    queryKey: ['scoringDocs'],
    queryFn: api.getScoringDocs,
    staleTime: 60_000,
  });

  if (isLoading) return <div className="loading-state">Loading parameter documentation…</div>;
  if (error || !data) {
    return (
      <Card>
        <CardBody>
          <div style={{ color: 'var(--neg)' }}>
            Failed to load scoring documentation: {error instanceof Error ? error.message : 'unknown error'}
          </div>
        </CardBody>
      </Card>
    );
  }

  const totalWeight = data.parameters.reduce((sum, p) => sum + p.weight, 0);

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--gap)' }}>
      <Card>
        <CardBody>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 16, alignItems: 'center', fontSize: 12.5 }}>
            <span style={{ color: 'var(--ink-4)' }}>Active weight profile:</span>
            <strong style={{ color: 'var(--ink-1)' }}>{data.active_profile}</strong>
            <span style={{ color: 'var(--ink-4)' }}>· regime:</span>
            <strong style={{ color: 'var(--ink-1)' }}>{data.regime_level}</strong>
            <span style={{ color: 'var(--ink-4)' }}>· weights sum to {(totalWeight * 100).toFixed(0)}%</span>
          </div>
          <div style={{ marginTop: 6, fontSize: 11.5, color: 'var(--ink-4)' }}>
            Weights below come live from <span className="mono">scoring_config.yaml</span>. Profile is
            chosen by the regime engine: <span className="mono">aggressive_swing</span> when risk-on,
            <span className="mono"> defensive_swing</span> otherwise.
          </div>
        </CardBody>
      </Card>

      <div className="grid-2" style={{ alignItems: 'stretch' }}>
        {data.parameters.map((p) => <ParameterCard key={p.key} param={p} />)}
      </div>
    </div>
  );
}
