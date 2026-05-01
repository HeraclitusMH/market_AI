import { Card, CardBody, CardHead } from '@/components/Card';
import { Badge } from '@/components/Badge';
import { ScoreBar } from '@/components/ScoreBar';
import { fmtTs } from '@/lib/formatters';
import type { RegimeCurrent, RegimeLevel } from '@/types/api';

export function regimeLabel(level: RegimeLevel | string | undefined): string {
  if (!level) return 'Unknown';
  return level
    .split('_')
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(' ');
}

export function regimeVariant(level: RegimeLevel | string | undefined): 'pos' | 'neg' | 'warn' | 'neutral' {
  if (level === 'risk_on') return 'pos';
  if (level === 'risk_off') return 'neg';
  if (level === 'risk_reduced') return 'warn';
  return 'neutral';
}

export function RegimeBadge({ level }: { level: RegimeLevel | string | undefined }) {
  return (
    <Badge variant={regimeVariant(level)} dot>
      {regimeLabel(level)}
    </Badge>
  );
}

function boolLabel(value: boolean | undefined): string {
  if (value === undefined) return '--';
  return value ? 'Allowed' : 'Blocked';
}

function effectRows(regime: RegimeCurrent): Array<[string, string, 'pos' | 'neg' | 'warn' | 'neutral']> {
  const effects = regime.effects;
  if (!effects) return [];
  return [
    ['Equity entries', boolLabel(effects.allows_new_equity_entries), effects.allows_new_equity_entries ? 'pos' : 'neg'],
    ['Options entries', boolLabel(effects.allows_new_options_entries), effects.allows_new_options_entries ? 'pos' : 'neg'],
    ['Sizing factor', `${(effects.sizing_factor * 100).toFixed(0)}%`, effects.sizing_factor > 0 ? 'neutral' : 'neg'],
    ['Score adjustment', `${effects.score_threshold_adjustment >= 0 ? '+' : ''}${(effects.score_threshold_adjustment * 100).toFixed(0)} pts`, effects.score_threshold_adjustment > 0 ? 'warn' : 'neutral'],
  ];
}

export function RegimeSummaryCard({ regime }: { regime: RegimeCurrent | undefined }) {
  const score = regime?.composite_score;
  const rows = regime ? effectRows(regime) : [];

  return (
    <Card>
      <CardHead
        title="Market Regime"
        subtitle={regime?.timestamp ? `Updated ${fmtTs(regime.timestamp)}` : 'No regime evaluation yet'}
        right={<RegimeBadge level={regime?.level} />}
      />
      <CardBody>
        {score == null ? (
          <div className="empty-state">{regime?.message ?? 'No regime snapshot available'}</div>
        ) : (
          <div className="regime-summary">
            <ScoreBar value={score / 100} />
            <div className="regime-meta">
              <Badge variant={regime?.data_quality === 'full' ? 'pos' : 'warn'}>
                {regime?.data_quality ?? 'unknown'} data
              </Badge>
              {regime?.hysteresis_active && <Badge variant="warn">Hysteresis active</Badge>}
              {regime?.transition && <Badge variant="info">{regime.transition}</Badge>}
            </div>
            {rows.length > 0 && (
              <div className="regime-effects-grid">
                {rows.map(([label, value, variant]) => (
                  <div key={label} className="regime-effect">
                    <span>{label}</span>
                    <Badge variant={variant}>{value}</Badge>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}
      </CardBody>
    </Card>
  );
}
