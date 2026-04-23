import React from 'react';

interface KPIProps {
  label: string;
  value: string;
  sub?: string;
  color?: 'pos' | 'neg' | 'neutral';
  sparkline?: React.ReactNode;
}

export function KPI({ label, value, sub, color, sparkline }: KPIProps) {
  return (
    <div className="kpi">
      <span className="kpi-label">{label}</span>
      <div>
        <div className={`kpi-value${color === 'pos' ? ' pos' : color === 'neg' ? ' neg' : ''}`}>
          {value}
        </div>
        {sub && <div className="kpi-sub">{sub}</div>}
      </div>
      {sparkline && <div>{sparkline}</div>}
    </div>
  );
}
