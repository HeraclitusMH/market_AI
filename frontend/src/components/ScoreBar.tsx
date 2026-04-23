import { colorForScore } from '@/lib/formatters';

interface ScoreBarProps {
  value: number;
  label?: string;
  showValue?: boolean;
}

export function ScoreBar({ value, label, showValue = true }: ScoreBarProps) {
  const pct = Math.round(Math.min(Math.max(value, 0), 1) * 100);
  const color = colorForScore(value);
  return (
    <div className="score-bar">
      {label && <span className="score-bar-label">{label}</span>}
      <div className="score-bar-track" role="progressbar" aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100}>
        <div className="score-bar-fill" style={{ width: `${pct}%`, background: color }} />
      </div>
      {showValue && <span className="score-bar-value">{pct}</span>}
    </div>
  );
}

interface FactorBarProps {
  name: string;
  value: number;
}

export function FactorBar({ name, value }: FactorBarProps) {
  const pct = Math.round(Math.min(Math.max(value, 0), 1) * 100);
  const color = colorForScore(value);
  return (
    <div className="factor-bar-row">
      <span className="factor-bar-name">{name}</span>
      <div className="factor-bar-track">
        <div className="factor-bar-fill" style={{ width: `${pct}%`, background: color }} />
      </div>
      <span className="factor-bar-val">{pct}</span>
    </div>
  );
}
