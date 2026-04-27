import { colorForScore } from '@/lib/formatters';

interface ScoreBarProps {
  value: number | null | undefined;
  label?: string;
  showValue?: boolean;
}

export function ScoreBar({ value, label, showValue = true }: ScoreBarProps) {
  const numeric = Number.isFinite(value) ? value as number : null;
  const pct = numeric == null ? 0 : Math.round(Math.min(Math.max(numeric, 0), 1) * 100);
  const color = numeric == null ? 'var(--ink-3)' : colorForScore(numeric);
  return (
    <div className="score-bar">
      {label && <span className="score-bar-label">{label}</span>}
      <div className="score-bar-track" role="progressbar" aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100}>
        <div className="score-bar-fill" style={{ width: `${pct}%`, background: color }} />
      </div>
      {showValue && <span className="score-bar-value">{numeric == null ? '--' : pct}</span>}
    </div>
  );
}

interface FactorBarProps {
  name: string;
  value: number | null | undefined;
  status?: string;
}

export function FactorBar({ name, value, status }: FactorBarProps) {
  const numeric = Number.isFinite(value) ? value as number : null;
  const pct = numeric == null ? 0 : Math.round(Math.min(Math.max(numeric, 0), 1) * 100);
  const color = numeric == null ? 'var(--ink-3)' : colorForScore(numeric);
  return (
    <div className="factor-bar-row">
      <span className="factor-bar-name">{name}</span>
      <div className="factor-bar-track">
        <div className="factor-bar-fill" style={{ width: `${pct}%`, background: color }} />
      </div>
      <span className="factor-bar-val" title={status}>{numeric == null ? '--' : pct}</span>
    </div>
  );
}
