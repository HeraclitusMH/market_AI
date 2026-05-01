import type {
  BotState, OverviewData, Position, Order, Fill, Signal,
  SentimentData, RiskData, RankingRow, PlanRow, ConfigData, ControlResponse,
  RegimeCurrent, RegimeHistoryRow,
} from '@/types/api';

const BASE = '/api/v1';

async function get<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE}${path}`);
  if (!res.ok) throw new Error(`${res.status} ${res.statusText} — ${path}`);
  return res.json() as Promise<T>;
}

async function post<T>(path: string, body?: unknown): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    method: 'POST',
    headers: body ? { 'Content-Type': 'application/json' } : {},
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) throw new Error(`${res.status} ${res.statusText} — ${path}`);
  return res.json() as Promise<T>;
}

export const api = {
  getOverview:    () => get<OverviewData>('/overview'),
  getPositions:   () => get<Position[]>('/positions'),
  getOrders:      (limit = 100) => get<Order[]>(`/orders?limit=${limit}`),
  getFills:       (limit = 100) => get<Fill[]>(`/fills?limit=${limit}`),
  getSignals:     (limit = 50)  => get<Signal[]>(`/signals?limit=${limit}`),
  getRankings:    (limit = 50)  => get<RankingRow[]>(`/rankings?limit=${limit}`),
  getTradePlans:  (limit = 50, status?: string) =>
    get<PlanRow[]>(`/trade-plans?limit=${limit}${status ? `&status=${status}` : ''}`),
  getSentiment:   () => get<SentimentData>('/sentiment'),
  getRisk:        () => get<RiskData>('/risk'),
  getRegimeCurrent: () => get<RegimeCurrent>('/regime/current'),
  getRegimeHistory: (days = 30) => get<RegimeHistoryRow[]>(`/regime/history?days=${days}`),
  getConfig:      () => get<ConfigData>('/config'),

  postControl: (action: string) => post<ControlResponse>(`/controls/${action}`),
  refreshSentiment: () => post<{ status: string; snapshots_written: number; reason: string }>(
    '/sentiment/refresh',
  ),
  refreshFundamentals: (symbol?: string) =>
    post<{
      refreshed: number;
      missing: number;
      errors: { symbol: string; error: string }[];
      duration_s: number;
      symbols: string[];
    }>(`/fundamentals/refresh${symbol ? `?symbol=${encodeURIComponent(symbol)}` : ''}`),
} as const;

export type ApiClient = typeof api;

export type BotStateApi = BotState;
