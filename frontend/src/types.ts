export interface Explanation {
  date: string;
  close: number;
  pattern: string | null;
  pattern_direction: string;
  pattern_description: string;
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
}
