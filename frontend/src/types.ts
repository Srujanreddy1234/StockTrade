export interface Explanation {
  date: string;
  close: number;
  pattern: string | null;
  pattern_direction: string;
  pattern_description: string;
  chart_pattern: string | null;
  chart_pattern_direction: string | null;
  trend: string;
  trend_agrees: boolean | null;
  trend_note: string;
  near_support: boolean;
  near_resistance: boolean;
  near_zone: string;
  support: number;
  resistance: number;
  volume_above_average: boolean;
  score: number;
  entry_zone: [number, number];
  invalidation: number | null;
  target1: number | null;
  target2: number | null;
  risk_reward: number | null;
  risk_reward_ok: boolean | null;
  risk_reward_note: string | null;
  status_reason: string | null;
  status: string;
  active_setup: boolean;
  monitoring: boolean;
  message: string | null;
  validated: string | null;
  validation_note: string | null;
  confluence_score: number | null;
  confluence_status: string | null;
  confluence_direction: string | null;
  confluence_reasons: string[];
}

export interface HistoryRow {
  timestamp: string;
  close: number;
  status: string;
  score: number;
  direction: string | null;
}

export interface Candle {
  timestamp: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
  ema: number | null;
  vwap: number | null;
  rsi: number | null;
}

export interface ChartLevels {
  support: number | null;
  resistance: number | null;
  entry_zone_low: number | null;
  entry_zone_high: number | null;
  target1: number | null;
  target2: number | null;
  invalidation: number | null;
}

export interface Chart {
  candles: Candle[];
  levels: ChartLevels;
}

export interface ScanResult {
  ticker: string;
  close: number;
  status: string | null;
  score: number | null;
  direction: string | null;
  validated: string | null;
  pattern: string | null;
  currency: Currency | null;
}

export interface ScanResponse {
  count: number;
  skipped: { ticker: string; error: string }[];
  results: ScanResult[];
}

export interface Currency {
  code: string;
  symbol: string;
}

export interface NewsItem {
  title: string | null;
  summary: string | null;
  link: string | null;
  publisher: string | null;
  published: number | null;
}

export interface LearnTopic {
  id: string;
  title: string;
  teaser: string;
  direction: string | null;
  what_is_it: string;
  why_it_matters: string;
  how_is_it_detected: string;
  confirmation: string[];
  invalidation: string[];
}

export interface Position {
  id: string;
  ticker: string;
  interval: string;
  direction: string;
  entry_price: number;
  target1: number;
  invalidation: number;
  entry_date: string;
  status: string;
  exit_price: number | null;
  exit_date: string | null;
  exit_reason: string | null;
  return_pct: number | null;
  unrealized_return_pct: number;
  display_return_pct: number;
}

export interface PositionsResponse {
  positions: Position[];
}

export interface AnalyzeResponse {
  source: string;
  interval: string;
  n_candles: number;
  ticker: string;
  currency: Currency | null;
  news: NewsItem[];
  explanation: Explanation;
  history: HistoryRow[];
  chart: Chart;
  alignment: AlignmentResult | null;
}

export interface AlignmentResult {
  ticker: string;
  daily_trend: string | null;
  hourly_trend: string | null;
  m15_trend: string | null;
  alignment: string | null;
  note: string;
}

export interface GrowwStatus {
  connected: boolean;
  real_trading_enabled: boolean;
}

export interface GrowwHolding {
  trading_symbol: string;
  company_name: string;
  quantity: number;
  average_price: number;
  invested_value: number;
}

export interface GrowwPosition {
  trading_symbol: string;
  exchange: string;
  segment: string;
  product: string;
  quantity: number;
  average_price: number;
  overnight_quantity: number;
  overnight_average_price: number;
}

export interface GrowwMargin {
  available_cash: number;
  used_margin: number;
  available_margin: number;
}

export interface AutonomousRiskLimits {
  max_capital_per_trade_pct: number;
  max_capital_per_trade_abs: number;
  daily_loss_limit_pct: number;
  max_open_positions: number;
  cooldown_minutes: number;
}

export interface AutonomousRiskState {
  date: string;
  day_start_margin: number | null;
  realized_pnl_today: number;
  kill_switch_active: boolean;
  kill_switch_reason: string | null;
}

export interface AutonomousStatus {
  mode: 'paper' | 'live';
  watchlist: string[];
  tick_interval_seconds: number;
  pipeline_refresh_seconds: number;
  buy_probability_threshold: number;
  sell_probability_threshold: number;
  risk_limits: AutonomousRiskLimits;
  risk_state: AutonomousRiskState;
  open_autonomous_positions: number;
}

export interface AutonomousEvent {
  id: number;
  ts: string;
  ticker: string;
  event_type: 'buy' | 'sell' | 'skip' | 'error' | 'observe';
  price: number | null;
  quantity: number | null;
  buy_probability: number | null;
  sell_probability: number | null;
  mode: 'paper' | 'live';
  order_id: string | null;
  reason: string | null;
}

export interface AutonomousEventsResponse {
  events: AutonomousEvent[];
}

export interface GrowwOrder {
  order_id: string;
  trading_symbol: string;
  exchange: string;
  segment: string;
  product: string;
  order_type: string;
  transaction_type: string;
  quantity: number;
  price: number | null;
  trigger_price: number | null;
  status: string;
  placed_at: string | null;
}
