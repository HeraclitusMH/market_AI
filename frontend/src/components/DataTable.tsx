import React, { useState, useMemo } from 'react';
import { ChevronUp, ChevronDown, ChevronsUpDown, ChevronRight } from 'lucide-react';

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
  expandRow?: (row: T) => React.ReactNode;
}

export function DataTable<T extends Record<string, unknown>>({
  data,
  columns,
  emptyMessage = 'No data',
  maxHeight,
  expandRow,
}: DataTableProps<T>) {
  const [sortKey, setSortKey] = useState<string | null>(null);
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('desc');
  const [expandedRows, setExpandedRows] = useState<Set<number>>(new Set());

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

  function toggleExpand(i: number, e: React.MouseEvent) {
    e.stopPropagation();
    setExpandedRows((prev) => {
      const next = new Set(prev);
      if (next.has(i)) next.delete(i); else next.add(i);
      return next;
    });
  }

  function SortIcon({ col }: { col: Column<T> }) {
    if (col.sortable === false) return null;
    if (sortKey !== col.key) return <ChevronsUpDown size={11} style={{ opacity: 0.4 }} />;
    return sortDir === 'asc' ? <ChevronUp size={11} /> : <ChevronDown size={11} />;
  }

  const colSpanTotal = columns.length + (expandRow ? 1 : 0);

  return (
    <div className="table-wrapper" style={maxHeight ? { maxHeight } : undefined}>
      <table>
        <thead>
          <tr>
            {expandRow && <th style={{ width: 32, padding: '8px 4px' }} />}
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
              <td colSpan={colSpanTotal} style={{ textAlign: 'center', color: 'var(--ink-4)', padding: '32px' }}>
                {emptyMessage}
              </td>
            </tr>
          ) : (
            sorted.flatMap((row, i): React.ReactNode[] => {
              const isExpanded = expandRow ? expandedRows.has(i) : false;
              const rows: React.ReactNode[] = [
                <tr key={i} onClick={expandRow ? (e) => toggleExpand(i, e) : undefined} style={expandRow ? { cursor: 'pointer' } : undefined}>
                  {expandRow && (
                    <td className="expand-toggle-cell">
                      <ChevronRight
                        size={13}
                        style={{
                          transition: 'transform 0.15s',
                          transform: isExpanded ? 'rotate(90deg)' : 'none',
                          display: 'block',
                        }}
                      />
                    </td>
                  )}
                  {columns.map((col) => (
                    <td key={col.key} className={col.numeric ? 'num' : ''}>
                      {col.render ? col.render(row) : String(row[col.key] ?? '')}
                    </td>
                  ))}
                </tr>,
              ];
              if (expandRow && isExpanded) {
                rows.push(
                  <tr key={`${i}-exp`} className="expanded-detail-row">
                    <td className="expand-toggle-cell" />
                    <td colSpan={columns.length}>
                      {expandRow(row)}
                    </td>
                  </tr>
                );
              }
              return rows;
            })
          )}
        </tbody>
      </table>
    </div>
  );
}
