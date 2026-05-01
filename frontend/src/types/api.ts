export interface BotState {
  paused: boolean;
  kill_switch: boolean;
  options_enabled: boolean;
  approve_mode: boolean;
  last_heartbeat: string | null;
}

export interface EquitySnapshot {
  timestamp: string;
  net_liquidation: number;
  cash: number;
  unrealized_pnl: number;
  realized_pnl: number;
  drawdown_pct: number;
}

export interface Position {
  symbol: string;
  name?: string;
  quantity: number;
  avg_cost: number;
  market_price: number;
  market_value: number;
  unrealized_pnl: number;
  instrument: string;
  updated_at: string | null;
}

export interface Order {
  id: number;
  intent_id: string;
  timestamp: string;
  symbol: string;
  name?: string;
  direction: string;
  instrument: string;
  quantity: number;
  order_type: string;
  limit_price: number | null;
  status: string;
  ibkr_order_id: number | null;
  max_loss: number;
}

export interface Fill {
  id: number;
  order_id: number;
  timestamp: string;
  symbol: string;
  name?: string;
  quantity: number;
  price: number;
  commission: number;
}

export interface Signal {
  id: number;
  timestamp: string;
  symbol: string;
  name?: string;
  score_total: number;
  components_json: string;
  regime: string;
  action: string;
  explanation: string;
}

export interface SentimentRow {
  id: number;
  timestamp: string;
  scope: string;
  key: string;
  score: number;
  summary: string;
  sources_json: string;
}

export interface LlmBudget {
  provider: string;
  model: string | null;
  month_to_date_eur: number;
  today_eur: number;
  monthly_cap_eur: number;
  daily_cap_eur: number;
  remaining_month_eur: number;
  remaining_today_eur: number;
  budget_stopped: boolean;
  reason: string | null;
}

export interface EventLog {
  id: number;
  timestamp: string;
  level: string;
  type: string;
  message: string;
}

export interface RankingRow {
  id: number;
  ts: string;
  symbol: string;
  name?: string;
  score_total: number;
  components: RankingComponents;
  eligible: boolean;
  reasons: string[];
}

export interface RankingComponents {
  [key: string]: RankingFactor | Composite7Factor | Record<string, number> | Record<string, unknown> | number | undefined;
  weights_used?: Record<string, number>;
  total_score?: number;
  composite_7factor?: Composite7Factor;
}

export interface RankingFactor {
  value_0_1?: number | null;
  status?: string;
  eligible?: boolean;
  reasons?: string[];
  metrics?: Record<string, unknown>;
  components?: Record<string, unknown>;
  raw_score?: number;
}

export interface Composite7Factor {
  symbol?: string;
  composite_score: number;
  regime: string;
  confidence: number;
  factors: Record<string, CompositeFactor>;
  timestamp?: string;
}

export interface CompositeFactor {
  score: number;
  weight: number;
  contribution: number;
  components: Record<string, unknown>;
}

export type RegimeLevel = 'risk_on' | 'risk_reduced' | 'risk_off' | 'unknown';

export interface RegimePillars {
  trend: number | null;
  breadth: number | null;
  volatility: number | null;
  credit_stress: number | null;
}

export interface RegimeEffects {
  allows_new_equity_entries: boolean;
  allows_new_options_entries: boolean;
  sizing_factor: number;
  stop_tightening_factor: number;
  score_threshold_adjustment: number;
}

export interface RegimeCurrent {
  level: RegimeLevel;
  message?: string;
  composite_score?: number;
  transition?: string | null;
  pillars?: RegimePillars;
  hysteresis_active?: boolean;
  data_quality?: string;
  timestamp?: string;
  effects?: RegimeEffects | null;
  components?: Record<string, unknown> | null;
}

export interface RegimeHistoryRow {
  timestamp: string;
  level: RegimeLevel;
  composite_score: number;
  trend: number | null;
  breadth: number | null;
  volatility: number | null;
  credit_stress: number | null;
  transition: string | null;
}

export interface PlanRow {
  id: number;
  ts: string;
  symbol: string;
  name?: string;
  bias: string;
  strategy: string;
  expiry: string | null;
  dte: number | null;
  legs: Record<string, unknown>;
  pricing: Record<string, unknown>;
  rationale: Record<string, unknown>;
  status: string;
  skip_reason: string | null;
}

export interface OverviewData {
  bot: BotState;
  equity: EquitySnapshot | null;
  equity_history: EquitySnapshot[];
  positions: Position[];
  position_count: number;
  sentiment_provider: string;
  sentiment_llm_budget: LlmBudget | null;
  recent_events: EventLog[];
}

export interface SentimentPoint { timestamp: string; score: number; }
export interface Headline { title: string; score: number; }

export interface SentimentData {
  market: SentimentRow | null;
  sectors: SentimentRow[];
  tickers: SentimentRow[];
  headlines: Headline[];
  history: SentimentPoint[];
  budget: LlmBudget;
  provider: string;
}

export interface RiskConfigData {
  max_drawdown_pct: number;
  max_risk_per_trade_pct: number;
  max_positions: number;
  require_positive_cash: boolean;
}

export interface RiskData {
  current: EquitySnapshot | null;
  history: EquitySnapshot[];
  bot: BotState;
  risk_config: RiskConfigData;
  positions_used: number;
  positions_max: number;
}

export interface ConfigData {
  sections: Record<string, Record<string, unknown>>;
}

export interface ControlResponse {
  ok: boolean;
  bot: BotState;
}
