import {
  AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer, Legend,
} from 'recharts';

interface LineSpec {
  key: string;
  name: string;
  color: string;
  yAxisId?: string;
}

interface LineChartProps {
  data: Record<string, unknown>[];
  lines: LineSpec[];
  xKey?: string;
  height?: number;
}

export function LineChart({ data, lines, xKey = 'x', height = 260 }: LineChartProps) {
  return (
    <ResponsiveContainer width="100%" height={height}>
      <AreaChart data={data} margin={{ top: 4, right: 8, left: 0, bottom: 0 }}>
        <defs>
          {lines.map((l) => (
            <linearGradient key={l.key} id={`grad-${l.key}`} x1="0" y1="0" x2="0" y2="1">
              <stop offset="5%" stopColor={l.color} stopOpacity={0.18} />
              <stop offset="95%" stopColor={l.color} stopOpacity={0} />
            </linearGradient>
          ))}
        </defs>
        <CartesianGrid strokeDasharray="3 3" stroke="var(--line-soft)" vertical={false} />
        <XAxis
          dataKey={xKey}
          tick={{ fill: 'var(--ink-4)', fontSize: 11, fontFamily: 'JetBrains Mono' }}
          axisLine={false}
          tickLine={false}
        />
        <YAxis
          tick={{ fill: 'var(--ink-4)', fontSize: 11, fontFamily: 'JetBrains Mono' }}
          axisLine={false}
          tickLine={false}
          width={56}
          yAxisId="left"
        />
        {lines.some((l) => l.yAxisId === 'right') && (
          <YAxis
            orientation="right"
            tick={{ fill: 'var(--ink-4)', fontSize: 11, fontFamily: 'JetBrains Mono' }}
            axisLine={false}
            tickLine={false}
            width={40}
            yAxisId="right"
          />
        )}
        <Tooltip
          contentStyle={{
            background: 'var(--bg-3)',
            border: '1px solid var(--line)',
            borderRadius: 6,
            fontSize: 12,
            color: 'var(--ink-2)',
          }}
          labelStyle={{ color: 'var(--ink-3)' }}
        />
        <Legend wrapperStyle={{ fontSize: 12, color: 'var(--ink-4)' }} />
        {lines.map((l) => (
          <Area
            key={l.key}
            type="monotone"
            dataKey={l.key}
            name={l.name}
            stroke={l.color}
            strokeWidth={1.5}
            fill={`url(#grad-${l.key})`}
            dot={false}
            yAxisId={l.yAxisId ?? 'left'}
          />
        ))}
      </AreaChart>
    </ResponsiveContainer>
  );
}
