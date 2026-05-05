import { useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Download, Search, ChevronUp, ChevronDown, ChevronsUpDown } from 'lucide-react';
import { api } from '@/lib/api';
import { fmtTs } from '@/lib/formatters';
import { Card, CardHead, CardBody } from '@/components/Card';
import { Button } from '@/components/Button';
import { Badge } from '@/components/Badge';
import type {
  RankingRow, Composite6Factor, ScoringWeightsResponse, WeightProfileWeights,
} from '@/types/api';

const FACTOR_KEYS: ReadonlyArray<keyof WeightProfileWeights> = [
  'technical', 'momentum', 'sentiment', 'quality', 'growth', 'risk_penalty',
];

const FACTOR_LABELS: Record<keyof WeightProfileWeights, string> = {
  technical: 'Tech',
  momentum: 'Mom',
  sentiment: 'Sent',
  quality: 'Qual',
  growth: 'Growth',
  risk_penalty: 'Risk',
};

const FACTOR_FULL_LABEL: Record<keyof WeightProfileWeights, string> = {
  technical: 'Technical',
  momentum: 'Momentum',
  sentiment: 'Sentiment',
  quality: 'Quality',
  growth: 'Growth',
  risk_penalty: 'Risk Penalty',
};

const PAGE_SIZE = 50;

interface FlatRow {
  id: number;
  rank: number;
  symbol: string;
  name: string;
  composite: number;
  technical: number | null;
  momentum: number | null;
  sentiment: number | null;
  quality: number | null;
  growth: number | null;
  risk_penalty: number | null;
  ts: string;
  eligible: boolean;
}

function isComposite6Factor(value: unknown): value is Composite6Factor {
  return value !== null && typeof value === 'object'
    && 'composite_score' in (value as object)
    && 'factors' in (value as object);
}

function flatten(row: RankingRow, rank: number): FlatRow {
  const composite = row.components?.composite_6factor;
  const factors = isComposite6Factor(composite) ? composite.factors : {};
  function pick(key: keyof WeightProfileWeights): number | null {
    const f = factors[key];
    if (!f || typeof f.score !== 'number') return null;
    return f.score;
  }
  return {
    id: row.id,
    rank,
    symbol: row.symbol,
    name: row.name ?? '',
    composite: row.score_total,
    technical: pick('technical'),
    momentum: pick('momentum'),
    sentiment: pick('sentiment'),
    quality: pick('quality'),
    growth: pick('growth'),
    risk_penalty: pick('risk_penalty'),
    ts: row.ts,
    eligible: row.eligible,
  };
}

function heatmapStyle(score: number | null, inverted = false): React.CSSProperties {
  if (score == null) return { color: 'var(--ink-4)' };
  const v = Math.min(Math.max(score, 0), 1);
  // Map 0..1 to a green/yellow/red band; invert for risk_penalty (higher = worse)
  const goodness = inverted ? 1 - v : v;
  let bg: string;
  let fg: string;
  if (goodness >= 0.7) { bg = 'rgba(34, 197, 94, 0.18)'; fg = 'var(--pos)'; }
  else if (goodness >= 0.4) { bg = 'rgba(234, 179, 8, 0.16)'; fg = 'var(--warn)'; }
  else { bg = 'rgba(239, 68, 68, 0.16)'; fg = 'var(--neg)'; }
  return {
    background: bg,
    color: fg,
    fontWeight: 600,
    textAlign: 'center',
    padding: '4px 6px',
    borderRadius: 3,
    fontVariantNumeric: 'tabular-nums',
  };
}

function csvEscape(v: string | number | null): string {
  if (v == null) return '';
  const s = String(v);
  if (/[,"\n]/.test(s)) return `"${s.replace(/"/g, '""')}"`;
  return s;
}

function downloadCsv(rows: FlatRow[]) {
  const header = ['Rank', 'Symbol', 'Name', 'Composite', 'Technical', 'Momentum', 'Sentiment', 'Quality', 'Growth', 'RiskPenalty', 'Eligible', 'LastUpdated'];
  const lines = [header.join(',')];
  for (const r of rows) {
    lines.push([
      r.rank, r.symbol, r.name,
      r.composite.toFixed(4),
      r.technical?.toFixed(4) ?? '',
      r.momentum?.toFixed(4) ?? '',
      r.sentiment?.toFixed(4) ?? '',
      r.quality?.toFixed(4) ?? '',
      r.growth?.toFixed(4) ?? '',
      r.risk_penalty?.toFixed(4) ?? '',
      r.eligible ? 'true' : 'false',
      r.ts,
    ].map(csvEscape).join(','));
  }
  const blob = new Blob([lines.join('\n')], { type: 'text/csv;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  const ts = new Date().toISOString().replace(/[:.]/g, '-');
  a.download = `rankings-${ts}.csv`;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

type SortKey = keyof FlatRow;

interface SortableHeaderProps {
  label: string;
  sortKey: SortKey;
  current: SortKey;
  dir: 'asc' | 'desc';
  onSort: (key: SortKey) => void;
  title?: string;
  numeric?: boolean;
}

function SortableHeader({ label, sortKey, current, dir, onSort, title, numeric }: SortableHeaderProps) {
  const active = current === sortKey;
  return (
    <th
      scope="col"
      className={`sortable${numeric ? ' num' : ''}`}
      onClick={() => onSort(sortKey)}
      title={title}
    >
      <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
        {label}
        {!active && <ChevronsUpDown size={11} style={{ opacity: 0.4 }} />}
        {active && (dir === 'asc' ? <ChevronUp size={11} /> : <ChevronDown size={11} />)}
      </span>
    </th>
  );
}

export function RankingsTab() {
  const { data: rankings = [], isLoading, error } = useQuery({
    queryKey: ['rankings'],
    queryFn: () => api.getRankings(2000),
    refetchInterval: 30_000,
  });
  const { data: weightsData } = useQuery({
    queryKey: ['scoringWeights'],
    queryFn: api.getScoringWeights,
    staleTime: 60_000,
  });
  const weights: ScoringWeightsResponse | undefined = weightsData;

  const [search, setSearch] = useState('');
  const [page, setPage] = useState(0);
  const [sortKey, setSortKey] = useState<SortKey>('composite');
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('desc');

  const flat = useMemo<FlatRow[]>(() => {
    return [...rankings]
      .sort((a, b) => b.score_total - a.score_total)
      .map((r, i) => flatten(r, i + 1));
  }, [rankings]);

  const filtered = useMemo<FlatRow[]>(() => {
    const q = search.trim().toLowerCase();
    if (!q) return flat;
    return flat.filter((r) =>
      r.symbol.toLowerCase().includes(q) || r.name.toLowerCase().includes(q),
    );
  }, [flat, search]);

  const sorted = useMemo<FlatRow[]>(() => {
    const arr = [...filtered];
    arr.sort((a, b) => {
      const av = a[sortKey];
      const bv = b[sortKey];
      let cmp: number;
      if (av == null && bv == null) cmp = 0;
      else if (av == null) cmp = 1;
      else if (bv == null) cmp = -1;
      else if (typeof av === 'number' && typeof bv === 'number') cmp = av - bv;
      else if (typeof av === 'boolean' && typeof bv === 'boolean') cmp = Number(av) - Number(bv);
      else cmp = String(av).localeCompare(String(bv));
      return sortDir === 'asc' ? cmp : -cmp;
    });
    return arr;
  }, [filtered, sortKey, sortDir]);

  function handleSort(key: SortKey) {
    if (key === sortKey) {
      setSortDir((d) => (d === 'asc' ? 'desc' : 'asc'));
    } else {
      setSortKey(key);
      setSortDir(key === 'symbol' || key === 'name' ? 'asc' : 'desc');
    }
    setPage(0);
  }

  const pageCount = Math.max(1, Math.ceil(sorted.length / PAGE_SIZE));
  const safePage = Math.min(page, pageCount - 1);
  const paginated = sorted.slice(safePage * PAGE_SIZE, (safePage + 1) * PAGE_SIZE);

  if (isLoading) return <div className="loading-state">Loading rankings…</div>;
  if (error) {
    return (
      <Card>
        <CardBody>
          <div style={{ color: 'var(--neg)', marginBottom: 10 }}>
            Failed to load rankings: {error instanceof Error ? error.message : 'unknown error'}
          </div>
          <Button onClick={() => window.location.reload()}>Retry</Button>
        </CardBody>
      </Card>
    );
  }

  function weightTitle(key: keyof WeightProfileWeights): string {
    const w = weights?.active_weights[key];
    if (w == null) return FACTOR_FULL_LABEL[key];
    return `${FACTOR_FULL_LABEL[key]} — ${(w * 100).toFixed(0)}% weight`;
  }

  return (
    <Card>
      <CardHead
        title="Full Rankings — parameter breakdown"
        subtitle={
          weights
            ? `${sorted.length} of ${flat.length} symbols · profile ${weights.active_profile}`
            : `${sorted.length} of ${flat.length} symbols`
        }
        right={
          <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
            <div style={{ position: 'relative' }}>
              <Search
                size={13}
                style={{
                  position: 'absolute', left: 8, top: '50%', transform: 'translateY(-50%)',
                  color: 'var(--ink-4)', pointerEvents: 'none',
                }}
              />
              <input
                type="search"
                value={search}
                onChange={(e) => { setSearch(e.target.value); setPage(0); }}
                placeholder="Filter ticker or name"
                aria-label="Filter ticker or name"
                style={{
                  padding: '5px 8px 5px 26px',
                  fontSize: 12,
                  background: 'var(--bg-1)',
                  border: '1px solid var(--line-soft)',
                  borderRadius: 4,
                  color: 'var(--ink-1)',
                  width: 200,
                }}
              />
            </div>
            <Button
              size="sm"
              variant="ghost"
              icon={<Download size={13} />}
              onClick={() => downloadCsv(sorted)}
              disabled={sorted.length === 0}
            >
              Export CSV
            </Button>
          </div>
        }
      />
      <CardBody flush>
        <div className="table-wrapper">
          <table>
            <thead>
              <tr>
                <SortableHeader label="#" sortKey="rank" current={sortKey} dir={sortDir} onSort={handleSort} numeric />
                <SortableHeader label="Symbol" sortKey="symbol" current={sortKey} dir={sortDir} onSort={handleSort} />
                <SortableHeader label="Name" sortKey="name" current={sortKey} dir={sortDir} onSort={handleSort} />
                <SortableHeader label="Composite" sortKey="composite" current={sortKey} dir={sortDir} onSort={handleSort} numeric title="Final weighted composite score (0-100)" />
                {FACTOR_KEYS.map((k) => (
                  <SortableHeader
                    key={k}
                    label={FACTOR_LABELS[k]}
                    sortKey={k as SortKey}
                    current={sortKey}
                    dir={sortDir}
                    onSort={handleSort}
                    numeric
                    title={weightTitle(k)}
                  />
                ))}
                <SortableHeader label="Eligible" sortKey="eligible" current={sortKey} dir={sortDir} onSort={handleSort} />
                <SortableHeader label="Updated" sortKey="ts" current={sortKey} dir={sortDir} onSort={handleSort} />
              </tr>
            </thead>
            <tbody>
              {paginated.length === 0 ? (
                <tr>
                  <td colSpan={11} style={{ textAlign: 'center', color: 'var(--ink-4)', padding: 32 }}>
                    No rankings match your filter.
                  </td>
                </tr>
              ) : paginated.map((r) => (
                <tr key={r.id}>
                  <td className="num mono" style={{ fontSize: 11.5, color: 'var(--ink-4)' }}>#{r.rank}</td>
                  <td><strong style={{ color: 'var(--ink-1)' }}>{r.symbol}</strong></td>
                  <td style={{ color: 'var(--ink-2)', fontSize: 12 }}>{r.name || '-'}</td>
                  <td className="num"><span style={heatmapStyle(r.composite)}>{Math.round(r.composite * 100)}</span></td>
                  {FACTOR_KEYS.map((k) => {
                    const v = r[k];
                    const inverted = k === 'risk_penalty';
                    return (
                      <td key={k} className="num">
                        <span style={heatmapStyle(v, inverted)}>
                          {v == null ? '-' : Math.round(v * 100)}
                        </span>
                      </td>
                    );
                  })}
                  <td>
                    <Badge variant={r.eligible ? 'pos' : 'neutral'} dot>
                      {r.eligible ? 'Yes' : 'No'}
                    </Badge>
                  </td>
                  <td className="mono" style={{ fontSize: 11, color: 'var(--ink-4)' }}>{fmtTs(r.ts)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        {pageCount > 1 && (
          <div
            style={{
              display: 'flex', justifyContent: 'space-between', alignItems: 'center',
              padding: '10px 16px', borderTop: '1px solid var(--line-soft)', fontSize: 12,
            }}
          >
            <button
              className="btn sm ghost"
              onClick={() => setPage((p) => Math.max(0, p - 1))}
              disabled={safePage === 0}
            >
              ← Prev
            </button>
            <span style={{ color: 'var(--ink-4)' }}>
              Page {safePage + 1} of {pageCount} · {sorted.length} rows
            </span>
            <button
              className="btn sm ghost"
              onClick={() => setPage((p) => Math.min(pageCount - 1, p + 1))}
              disabled={safePage >= pageCount - 1}
            >
              Next →
            </button>
          </div>
        )}
      </CardBody>
    </Card>
  );
}
