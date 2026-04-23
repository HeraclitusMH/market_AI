import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { api } from '@/lib/api';
import { fmtMoney, fmtTs } from '@/lib/formatters';
import { KPI } from '@/components/KPI';
import { Card, CardHead, CardBody } from '@/components/Card';
import { DataTable, type Column } from '@/components/DataTable';
import { SegmentedControl } from '@/components/SegmentedControl';
import { statusBadge, actionBadge } from '@/components/Badge';
import type { Order, Fill } from '@/types/api';

const TABS = [{ value: 'orders', label: 'Orders' }, { value: 'fills', label: 'Fills' }];

const ORDER_COLS: Column<Order>[] = [
  { key: 'timestamp', header: 'Time', render: (r) => <span className="mono" style={{ fontSize: 11.5 }}>{fmtTs(r.timestamp)}</span> },
  { key: 'symbol', header: 'Symbol', render: (r) => <strong style={{ color: 'var(--ink-1)' }}>{r.symbol}</strong> },
  { key: 'direction', header: 'Side', render: (r) => actionBadge(r.direction) },
  { key: 'instrument', header: 'Instr' },
  { key: 'quantity', header: 'Qty', numeric: true },
  { key: 'order_type', header: 'Type' },
  { key: 'limit_price', header: 'Limit', numeric: true, render: (r) => r.limit_price != null ? fmtMoney(r.limit_price) : '—' },
  { key: 'max_loss', header: 'Max Loss', numeric: true, render: (r) => fmtMoney(r.max_loss) },
  { key: 'status', header: 'Status', render: (r) => statusBadge(r.status) },
  { key: 'intent_id', header: 'Intent ID', render: (r) => <span className="mono" style={{ fontSize: 10.5, color: 'var(--ink-5)' }}>{r.intent_id.slice(-8)}</span> },
];

const FILL_COLS: Column<Fill>[] = [
  { key: 'timestamp', header: 'Time', render: (r) => <span className="mono" style={{ fontSize: 11.5 }}>{fmtTs(r.timestamp)}</span> },
  { key: 'symbol', header: 'Symbol', render: (r) => <strong style={{ color: 'var(--ink-1)' }}>{r.symbol}</strong> },
  { key: 'quantity', header: 'Qty', numeric: true },
  { key: 'price', header: 'Price', numeric: true, render: (r) => fmtMoney(r.price) },
  { key: 'commission', header: 'Comm', numeric: true, render: (r) => fmtMoney(r.commission) },
  { key: 'order_id', header: 'Order ID', numeric: true },
];

export function Orders() {
  const [tab, setTab] = useState('orders');
  const { data: orders = [], isLoading: ordLoading } = useQuery({ queryKey: ['orders'], queryFn: () => api.getOrders(200), refetchInterval: 20_000 });
  const { data: fills = [], isLoading: fillLoading } = useQuery({ queryKey: ['fills'], queryFn: () => api.getFills(200), refetchInterval: 20_000 });

  const isLoading = ordLoading || fillLoading;
  if (isLoading) return <div className="loading-state">Loading orders…</div>;

  const pending = orders.filter((o) => o.status === 'pending_approval' || o.status === 'pending').length;
  const filled = orders.filter((o) => o.status === 'filled').length;
  const totalComm = fills.reduce((s, f) => s + f.commission, 0);

  return (
    <div>
      <h1 className="page-title">Orders</h1>

      <div className="kpi-grid">
        <KPI label="Total Orders" value={String(orders.length)} />
        <KPI label="Pending Approval" value={String(pending)} color={pending > 0 ? 'neg' : 'neutral'} />
        <KPI label="Filled" value={String(filled)} />
        <KPI label="Total Commission" value={fmtMoney(totalComm)} />
      </div>

      <Card>
        <CardHead
          title={tab === 'orders' ? 'Orders' : 'Fills'}
          subtitle={tab === 'orders' ? `${orders.length} total` : `${fills.length} total`}
          right={<SegmentedControl options={TABS} value={tab} onChange={setTab} aria-label="Tab" />}
        />
        <CardBody flush>
          {tab === 'orders' ? (
            <DataTable
              data={orders as unknown as Record<string, unknown>[]}
              columns={ORDER_COLS as unknown as Column<Record<string, unknown>>[]}
              emptyMessage="No orders"
            />
          ) : (
            <DataTable
              data={fills as unknown as Record<string, unknown>[]}
              columns={FILL_COLS as unknown as Column<Record<string, unknown>>[]}
              emptyMessage="No fills"
            />
          )}
        </CardBody>
      </Card>
    </div>
  );
}
