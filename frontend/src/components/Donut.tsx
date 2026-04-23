interface DonutProps {
  value: number;
  max: number;
  color?: string;
  label?: string;
  size?: number;
}

export function Donut({ value, max, color = 'var(--accent)', label, size = 120 }: DonutProps) {
  const pct = max > 0 ? Math.min(value / max, 1) : 0;
  const r = size / 2 - 10;
  const circumference = 2 * Math.PI * r;
  const filled = pct * circumference;
  const cx = size / 2;
  const cy = size / 2;

  return (
    <div className="donut-wrapper" style={{ width: size, height: size }}>
      <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} aria-hidden="true">
        <circle cx={cx} cy={cy} r={r} fill="none" stroke="var(--bg-4)" strokeWidth="9" />
        <circle
          cx={cx}
          cy={cy}
          r={r}
          fill="none"
          stroke={color}
          strokeWidth="9"
          strokeDasharray={`${filled} ${circumference}`}
          strokeLinecap="round"
          transform={`rotate(-90 ${cx} ${cy})`}
        />
      </svg>
      <div className="donut-center">
        <span className="donut-pct">{Math.round(pct * 100)}%</span>
        {label && <span className="donut-caption">{label}</span>}
      </div>
    </div>
  );
}
