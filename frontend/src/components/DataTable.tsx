import React, { useState, useMemo } from 'react';
import { ChevronUp, ChevronDown, ChevronsUpDown } from 'lucide-react';

export interface Column<T> {
  key: string;
  header: string;
  numeric?: boolean;
  sortable?: boolean;
  render?: (row: T) => React.ReactNode;
}

interface DataTableProps<T extends Record<string, unknown>> {
  data: T[];
  columns: Column<T>[];
  emptyMessage?: string;
  maxHeight?: number;
}

export function DataTable<T extends Record<string, unknown>>({
  data,
  columns,
  emptyMessage = 'No data',
  maxHeight,
}: DataTableProps<T>) {
  const [sortKey, setSortKey] = useState<string | null>(null);
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('desc');

  const sorted = useMemo(() => {
    if (!sortKey) return data;
    return [...data].sort((a, b) => {
      const av = a[sortKey];
      const bv = b[sortKey];
      if (typeof av === 'number' && typeof bv === 'number') {
        return sortDir === 'asc' ? av - bv : bv - av;
      }
      const as = String(av ?? '');
      const bs = String(bv ?? '');
      return sortDir === 'asc' ? as.localeCompare(bs) : bs.localeCompare(as);
    });
  }, [data, sortKey, sortDir]);

  function handleSort(key: string) {
    if (sortKey === key) {
      setSortDir((d) => (d === 'asc' ? 'desc' : 'asc'));
    } else {
      setSortKey(key);
      setSortDir('desc');
    }
  }

  function SortIcon({ col }: { col: Column<T> }) {
    if (col.sortable === false) return null;
    if (sortKey !== col.key) return <ChevronsUpDown size={11} style={{ opacity: 0.4 }} />;
    return sortDir === 'asc' ? <ChevronUp size={11} /> : <ChevronDown size={11} />;
  }

  return (
    <div className="table-wrapper" style={maxHeight ? { maxHeight } : undefined}>
      <table>
        <thead>
          <tr>
            {columns.map((col) => (
              <th
                key={col.key}
                scope="col"
                className={`${col.numeric ? 'num' : ''}${col.sortable !== false ? ' sortable' : ''}`}
                onClick={col.sortable !== false ? () => handleSort(col.key) : undefined}
              >
                <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
                  {col.header}
                  <SortIcon col={col} />
                </span>
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {sorted.length === 0 ? (
            <tr>
              <td colSpan={columns.length} style={{ textAlign: 'center', color: 'var(--ink-4)', padding: '32px' }}>
                {emptyMessage}
              </td>
            </tr>
          ) : (
            sorted.map((row, i) => (
              <tr key={i}>
                {columns.map((col) => (
                  <td key={col.key} className={col.numeric ? 'num' : ''}>
                    {col.render ? col.render(row) : String(row[col.key] ?? '')}
                  </td>
                ))}
              </tr>
            ))
          )}
        </tbody>
      </table>
    </div>
  );
}
