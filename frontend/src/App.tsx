import { useEffect, useRef, useState } from 'react';
import type {
  AnalyzeResponse,
  AutonomousEvent,
  AutonomousEventsResponse,
  AutonomousStatus,
  Explanation,
  GrowwStatus,
  LearnTopic,
  Position,
  PositionsResponse,
  ScanResult,
  ScanResponse,
} from './types';
import CandleChart from './CandleChart';
import './App.css';

// API base URL comes from the build-time env var VITE_API_BASE_URL so the
// deployed backend URL is never baked into source. Falls back to localhost for
// local `npm run dev` without the var set.
const API_BASE = (import.meta.env.VITE_API_BASE_URL ?? 'http://127.0.0.1:8000').replace(/\/+$/, '');
const API_KEY = (import.meta.env.VITE_BACKEND_API_KEY ?? '').trim();

const apiHeaders: Record<string, string> = {
  'Content-Type': 'application/json',
};
if (API_KEY) {
  apiHeaders['X-API-Key'] = API_KEY;
}

const TICKERS: { market: string; flag: string; ticker: string }[] = [
  { market: 'India (NSE)', flag: '🇮🇳', ticker: 'RELIANCE.NS' },
  { market: 'USA (NASDAQ)', flag: '🇺🇸', ticker: 'AAPL' },
  { market: 'USA (NYSE)', flag: '🇺🇸', ticker: 'TSLA' },
  { market: 'UK', flag: '🇬🇧', ticker: 'HSBA.L' },
  { market: 'Japan', flag: '🇯🇵', ticker: '7203.T' },
  { market: 'Germany', flag: '🇩🇪', ticker: 'SAP.DE' },
  { market: 'Canada', flag: '🇨🇦', ticker: 'SHOP.TO' },
];

const LS = {
  source: 'tta_source',
  ticker: 'tta_ticker',
  interval: 'tta_interval',
};

function readLS(key: string, fallback: string) {
  try {
    return localStorage.getItem(key) || fallback;
  } catch {
    return fallback;
  }
}

function loadCached(): AnalyzeResponse | null {
  try {
    const raw = localStorage.getItem('tta_last');
    return raw ? (JSON.parse(raw) as AnalyzeResponse) : null;
  } catch {
    return null;
  }
}

function App() {
  const [data, setData] = useState<AnalyzeResponse | null>(() => loadCached());
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [stale, setStale] = useState(false);
  const [showWhy, setShowWhy] = useState(false);
  const [source, setSource] = useState(() => readLS(LS.source, 'synthetic'));
  const [ticker, setTicker] = useState(() => readLS(LS.ticker, 'RELIANCE.NS'));
  const [intervalVal, setIntervalVal] = useState(() => readLS(LS.interval, '1m'));
  const [view, setView] = useState<'single' | 'scanner' | 'learn' | 'positions' | 'live' | 'autonomous'>('single');
  const [scanData, setScanData] = useState<ScanResult[] | null>(null);
  const [scanLoading, setScanLoading] = useState(false);
  const [scanError, setScanError] = useState<string | null>(null);
  const [learnTopics, setLearnTopics] = useState<LearnTopic[] | null>(null);
  const [learnLoading, setLearnLoading] = useState(false);
  const [learnError, setLearnError] = useState<string | null>(null);
  const [selectedTopic, setSelectedTopic] = useState<LearnTopic | null>(null);
  const [positions, setPositions] = useState<Position[] | null>(null);
  const [positionsLoading, setPositionsLoading] = useState(false);
  const [positionsError, setPositionsError] = useState<string | null>(null);
  const [openPositionLoading, setOpenPositionLoading] = useState(false);

  // Live / Groww state
  const [growwStatus, setGrowwStatus] = useState<GrowwStatus | null>(null);
  const [growwHoldings, setGrowwHoldings] = useState<any[] | null>(null);
  const [growwPositions, setGrowwPositions] = useState<any[] | null>(null);
  const [growwMargin, setGrowwMargin] = useState<any | null>(null);
  const [growwOrders, setGrowwOrders] = useState<any[] | null>(null);
  const [growwLoading, setGrowwLoading] = useState(false);
  const [growwError, setGrowwError] = useState<string | null>(null);
  const [orderForm, setOrderForm] = useState({
    trading_symbol: 'RELIANCE.NS',
    exchange: 'NSE',
    segment: 'EQ',
    product: 'CNC',
    order_type: 'LIMIT',
    transaction_type: 'BUY',
    quantity: 1,
    price: '',
    trigger_price: '',
  });

  // Autonomous trading loop state
  const [autoStatus, setAutoStatus] = useState<AutonomousStatus | null>(null);
  const [autoEvents, setAutoEvents] = useState<AutonomousEvent[] | null>(null);
  const [autoLoading, setAutoLoading] = useState(false);
  const [autoError, setAutoError] = useState<string | null>(null);
  const [killSwitchResetting, setKillSwitchResetting] = useState(false);

  const fetched = useRef(false);

  const fetchData = async (overrides?: {
    source?: string;
    ticker?: string;
    interval?: string;
  }) => {
    const src = overrides?.source ?? source;
    const tk = overrides?.ticker ?? ticker;
    const iv = overrides?.interval ?? intervalVal;
    setLoading(true);
    setError(null);
    setStale(false);
    try {
      const params = new URLSearchParams();
      if (src === 'yfinance') {
        params.set('ticker', tk);
        params.set('interval', iv);
      }
      const url =
        src === 'synthetic'
          ? `${API_BASE}/analyze/synthetic`
          : `${API_BASE}/analyze/${src}?${params.toString()}`;
      const res = await fetch(url, { headers: apiHeaders });
      if (!res.ok) throw new Error('HTTP ' + res.status);
      const json: AnalyzeResponse = await res.json();
      setData(json);
      setShowWhy(false);
      try {
        localStorage.setItem('tta_last', JSON.stringify(json));
      } catch {
        /* ignore storage errors */
      }
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : String(e);
      setError(msg);
      if (data) setStale(true);
    } finally {
      setLoading(false);
    }
  };

  const fetchScan = async () => {
    setScanLoading(true);
    setScanError(null);
    try {
      const res = await fetch(`${API_BASE}/scan`, { headers: apiHeaders });
      if (!res.ok) throw new Error('HTTP ' + res.status);
      const json: ScanResponse = await res.json();
      setScanData(json.results);
      const skipped = json.skipped?.length ?? 0;
      setScanError(skipped ? `${skipped} ticker(s) skipped (see console)` : null);
      if (skipped) console.warn('Scanner skipped:', json.skipped);
    } catch (e: unknown) {
      setScanError(e instanceof Error ? e.message : String(e));
    } finally {
      setScanLoading(false);
    }
  };

  const fetchLearnTopics = async () => {
    setLearnLoading(true);
    setLearnError(null);
    try {
      const res = await fetch(`${API_BASE}/learn`, { headers: apiHeaders });
      if (!res.ok) throw new Error('HTTP ' + res.status);
      const json = await res.json();
      setLearnTopics(json.topics);
    } catch (e: unknown) {
      setLearnError(e instanceof Error ? e.message : String(e));
    } finally {
      setLearnLoading(false);
    }
  };

  const openLearnTopic = async (topicId: string) => {
    setLearnLoading(true);
    setLearnError(null);
    try {
      const res = await fetch(`${API_BASE}/learn/${topicId}`, { headers: apiHeaders });
      if (!res.ok) throw new Error('HTTP ' + res.status);
      const json: LearnTopic = await res.json();
      setSelectedTopic(json);
    } catch (e: unknown) {
      setLearnError(e instanceof Error ? e.message : String(e));
    } finally {
      setLearnLoading(false);
    }
  };

  const fetchPositions = async () => {
    setPositionsLoading(true);
    setPositionsError(null);
    try {
      const res = await fetch(`${API_BASE}/positions`, { headers: apiHeaders });
      if (!res.ok) throw new Error('HTTP ' + res.status);
      const json: PositionsResponse = await res.json();
      setPositions(json.positions);
    } catch (e: unknown) {
      setPositionsError(e instanceof Error ? e.message : String(e));
    } finally {
      setPositionsLoading(false);
    }
  };

  const fetchGrowwStatus = async () => {
    setGrowwLoading(true);
    setGrowwError(null);
    try {
      const res = await fetch(`${API_BASE}/groww/status`, { headers: apiHeaders });
      if (!res.ok) throw new Error('HTTP ' + res.status);
      const json = await res.json();
      setGrowwStatus(json);
    } catch (e: unknown) {
      setGrowwError(e instanceof Error ? e.message : String(e));
    } finally {
      setGrowwLoading(false);
    }
  };

  const fetchGrowwHoldings = async () => {
    setGrowwLoading(true);
    setGrowwError(null);
    try {
      const res = await fetch(`${API_BASE}/groww/holdings`, { headers: apiHeaders });
      if (!res.ok) throw new Error('HTTP ' + res.status);
      const json = await res.json();
      setGrowwHoldings(json.holdings);
    } catch (e: unknown) {
      setGrowwError(e instanceof Error ? e.message : String(e));
    } finally {
      setGrowwLoading(false);
    }
  };

  const fetchGrowwPositions = async () => {
    setGrowwLoading(true);
    setGrowwError(null);
    try {
      const res = await fetch(`${API_BASE}/groww/positions`, { headers: apiHeaders });
      if (!res.ok) throw new Error('HTTP ' + res.status);
      const json = await res.json();
      setGrowwPositions(json.positions);
    } catch (e: unknown) {
      setGrowwError(e instanceof Error ? e.message : String(e));
    } finally {
      setGrowwLoading(false);
    }
  };

  const fetchGrowwMargin = async () => {
    setGrowwLoading(true);
    setGrowwError(null);
    try {
      const res = await fetch(`${API_BASE}/groww/margin`, { headers: apiHeaders });
      if (!res.ok) throw new Error('HTTP ' + res.status);
      const json = await res.json();
      setGrowwMargin(json);
    } catch (e: unknown) {
      setGrowwError(e instanceof Error ? e.message : String(e));
    } finally {
      setGrowwLoading(false);
    }
  };

  const fetchGrowwOrders = async () => {
    setGrowwLoading(true);
    setGrowwError(null);
    try {
      const res = await fetch(`${API_BASE}/groww/orders`, { headers: apiHeaders });
      if (!res.ok) throw new Error('HTTP ' + res.status);
      const json = await res.json();
      setGrowwOrders(json.orders);
    } catch (e: unknown) {
      setGrowwError(e instanceof Error ? e.message : String(e));
    } finally {
      setGrowwLoading(false);
    }
  };

  const fetchAutoStatus = async () => {
    try {
      const res = await fetch(`${API_BASE}/autonomous/status`, { headers: apiHeaders });
      if (!res.ok) throw new Error('HTTP ' + res.status);
      const json: AutonomousStatus = await res.json();
      setAutoStatus(json);
      setAutoError(null);
    } catch (e: unknown) {
      setAutoError(e instanceof Error ? e.message : String(e));
    }
  };

  const fetchAutoEvents = async () => {
    try {
      const res = await fetch(`${API_BASE}/autonomous/events?limit=200`, { headers: apiHeaders });
      if (!res.ok) throw new Error('HTTP ' + res.status);
      const json: AutonomousEventsResponse = await res.json();
      setAutoEvents(json.events);
      setAutoError(null);
    } catch (e: unknown) {
      setAutoError(e instanceof Error ? e.message : String(e));
    }
  };

  const refreshAutonomous = async (showSpinner = false) => {
    if (showSpinner) setAutoLoading(true);
    await Promise.all([fetchAutoStatus(), fetchAutoEvents()]);
    if (showSpinner) setAutoLoading(false);
  };

  const resetKillSwitch = async () => {
    setKillSwitchResetting(true);
    try {
      const res = await fetch(`${API_BASE}/autonomous/kill-switch/reset`, {
        method: 'POST',
        headers: apiHeaders,
      });
      if (!res.ok) throw new Error('HTTP ' + res.status);
      await fetchAutoStatus();
    } catch (e: unknown) {
      setAutoError(e instanceof Error ? e.message : String(e));
    } finally {
      setKillSwitchResetting(false);
    }
  };

  // Poll the autonomous loop's status + events every 5s while that tab is
  // open, so the dashboard feels live without hammering the backend on
  // every single 1s tick the trader process makes internally.
  useEffect(() => {
    if (view !== 'autonomous') return;
    refreshAutonomous(true);
    const id = setInterval(() => refreshAutonomous(false), 5000);
    return () => clearInterval(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [view]);

  const placeGrowwOrder = async () => {
    setGrowwLoading(true);
    setGrowwError(null);
    try {
      const body: any = {
        trading_symbol: orderForm.trading_symbol,
        exchange: orderForm.exchange,
        segment: orderForm.segment,
        product: orderForm.product,
        order_type: orderForm.order_type,
        transaction_type: orderForm.transaction_type,
        quantity: Number(orderForm.quantity),
      };
      if (orderForm.price !== '' && orderForm.price != null) body.price = Number(orderForm.price);
      if (orderForm.trigger_price !== '' && orderForm.trigger_price != null) body.trigger_price = Number(orderForm.trigger_price);
      const res = await fetch(`${API_BASE}/groww/orders`, {
        method: 'POST',
        headers: { ...apiHeaders, 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: 'HTTP ' + res.status }));
        throw new Error(err.detail || 'HTTP ' + res.status);
      }
      await fetchGrowwOrders();
      setOrderForm((f) => ({ ...f, quantity: 1, price: '', trigger_price: '' }));
    } catch (e: unknown) {
      setGrowwError(e instanceof Error ? e.message : String(e));
    } finally {
      setGrowwLoading(false);
    }
  };

  const openPosition = async () => {
    if (!data?.explanation?.active_setup || !data?.ticker) return;
    setOpenPositionLoading(true);
    try {
      const res = await fetch(`${API_BASE}/positions/open`, {
        method: 'POST',
        headers: { ...apiHeaders, 'Content-Type': 'application/json' },
        body: JSON.stringify({
          ticker: data.ticker,
          interval: data.interval,
        }),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: 'HTTP ' + res.status }));
        throw new Error(err.detail || 'HTTP ' + res.status);
      }
      await fetchPositions();
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : String(e);
      setPositionsError(msg);
    } finally {
      setOpenPositionLoading(false);
    }
  };

  const closePosition = async (positionId: string) => {
    setPositionsLoading(true);
    try {
      const res = await fetch(`${API_BASE}/positions/${positionId}/close`, {
        method: 'POST',
        headers: apiHeaders,
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: 'HTTP ' + res.status }));
        throw new Error(err.detail || 'HTTP ' + res.status);
      }
      await fetchPositions();
    } catch (e: unknown) {
      setPositionsError(e instanceof Error ? e.message : String(e));
    } finally {
      setPositionsLoading(false);
    }
  };

  // Clicking a scanner row loads that ticker into the single-stock view.
  const openTicker = (t: string) => {
    setSource('yfinance');
    setTicker(t);
    setIntervalVal('1m');
    setView('single');
    fetchData({ source: 'yfinance', ticker: t, interval: '1m' });
  };

  // Persist selections so a page refresh keeps the inputs and only the
  // feed (data) is re-fetched.
  useEffect(() => {
    try {
      localStorage.setItem(LS.source, source);
      localStorage.setItem(LS.ticker, ticker);
      localStorage.setItem(LS.interval, intervalVal);
    } catch {
      /* ignore storage errors */
    }
  }, [source, ticker, intervalVal]);

  // Auto-refresh the feed on first mount (and on every page load).
  useEffect(() => {
    if (fetched.current) return;
    fetched.current = true;
    fetchData();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const statusColor = (status: string) => {
    if (status === 'ENTRY') return '#16a34a';
    if (status === 'WATCH') return '#ca8a04';
    return '#6b7280';
  };

  const fmtPrice = (p: number) => {
    if (data?.currency) {
      return `${data.currency.symbol}${p.toFixed(2)} ${data.currency.code}`;
    }
    return p.toFixed(2);
  };

  const confluenceColor = (status: string | null) => {
    if (status === 'ENTRY') return '#16a34a';
    if (status === 'WATCH') return '#ca8a04';
    return '#6b7280';
  };

  const renderConfluence = (ex: Explanation) => {
    if (!ex.confluence_status) return null;
    const downgraded = ex.status !== ex.confluence_status;
    return (
      <div className="confluence-panel">
        <div className="confluence-header">
          <span
            className="confluence-badge"
            style={{ background: confluenceColor(ex.confluence_status) }}
          >
            Confluence: {ex.confluence_status}
          </span>
          <span className="confluence-score">{ex.confluence_score ?? 0}/100</span>
        </div>
        {downgraded && (
          <p className="confluence-downgrade-note">
            Base setup was {ex.status}, but structure/zone/breakout/pullback/volume/VWAP
            engines {ex.confluence_status === 'NO TRADE' ? 'contradicted it' : "didn't fully corroborate it"} —
            downgraded to {ex.confluence_status}.
          </p>
        )}
        <ul className="confluence-reasons">
          {(ex.confluence_reasons || []).map((r: string, i: number) => (
            <li key={i}>{r}</li>
          ))}
        </ul>
      </div>
    );
  };

  const renderExplanation = () => {
    if (!data) return null;
    const ex = data.explanation;

    if (ex.active_setup) {
      return (
        <div className="card active">
          <div className="card-header">
            <span className="status-badge" style={{ background: statusColor(ex.status) }}>
              {ex.status}
            </span>
            <span className="score">{ex.score}/100</span>
            {ex.validated === 'provisional' && (
              <span className="provisional-badge">Provisional</span>
            )}
            {ex.validated === 'experimental' && (
              <span className="experimental-badge">Experimental</span>
            )}
          </div>
          <div className="card-body">
            <p className="price">Close: {fmtPrice(ex.close)}</p>
            <p className="pattern-line">
              <strong>Candle pattern: {ex.pattern || '—'}</strong> ({ex.pattern_direction})
              {ex.pattern && (
                <button
                  className="learn-link"
                  onClick={() => {
                    setView('learn');
                    openLearnTopic(ex.pattern as string);
                  }}
                >
                  Learn about this pattern
                </button>
              )}
            </p>
            {ex.chart_pattern && (
              <p className="pattern-line chart-pattern-line">
                <strong>Chart pattern: {ex.chart_pattern}</strong> ({ex.chart_pattern_direction})
                <button
                  className="learn-link"
                  onClick={() => {
                    setView('learn');
                    openLearnTopic(ex.chart_pattern as string);
                  }}
                >
                  Learn about this pattern
                </button>
              </p>
            )}
            <p className="description">{ex.pattern_description}</p>
            <p className="meta">Trend: {ex.trend}</p>
            <div className="levels">
              <div><span>Entry zone</span><strong>{ex.entry_zone[0].toFixed(2)} – {ex.entry_zone[1].toFixed(2)}</strong></div>
              <div><span>Target 1</span><strong>{ex.target1?.toFixed(2) ?? '—'}</strong></div>
              <div><span>Target 2</span><strong>{ex.target2?.toFixed(2) ?? '—'}</strong></div>
              <div><span>Invalidation</span><strong>{ex.invalidation?.toFixed(2) ?? '—'}</strong></div>
              <div><span>Risk/Reward</span><strong>1:{ex.risk_reward?.toFixed(1) ?? '—'}</strong></div>
            </div>
            {ex.validation_note && (
              <div className="validation-note">
                <p>{ex.validation_note}</p>
              </div>
            )}
            {renderConfluence(ex)}
            <button className="paper-enter-btn" onClick={openPosition} disabled={openPositionLoading}>
              {openPositionLoading ? 'Entering…' : 'Enter this trade (paper)'}
            </button>
            <button className="why-btn" onClick={() => setShowWhy((v) => !v)}>
              {showWhy ? 'Hide' : 'Why?'}
            </button>
            {showWhy && (
              <div className="why-box">
                <p>{ex.trend_note}</p>
                {ex.risk_reward_note && <p>{ex.risk_reward_note}</p>}
              </div>
            )}
          </div>
        </div>
      );
    }

    if (ex.monitoring) {
      return (
        <div className="card monitoring">
          <div className="card-header">
            <span className="status-badge" style={{ background: statusColor(ex.status) }}>
              {ex.status}
            </span>
            <span className="score">{ex.score}/100</span>
            {ex.validated === 'provisional' && (
              <span className="provisional-badge">Provisional</span>
            )}
            {ex.validated === 'experimental' && (
              <span className="experimental-badge">Experimental</span>
            )}
          </div>
          <div className="card-body">
            <p className="price">Close: {fmtPrice(ex.close)}</p>
            {ex.pattern && (
              <p className="pattern-line">
                <strong>{ex.pattern}</strong> ({ex.pattern_direction})
                <button
                  className="learn-link"
                  onClick={() => {
                    setView('learn');
                    openLearnTopic(ex.pattern as string);
                  }}
                >
                  Learn about this pattern
                </button>
              </p>
            )}
            {ex.chart_pattern && (
              <p className="pattern-line chart-pattern-line">
                <strong>Chart pattern: {ex.chart_pattern}</strong> ({ex.chart_pattern_direction})
                <button
                  className="learn-link"
                  onClick={() => {
                    setView('learn');
                    openLearnTopic(ex.chart_pattern as string);
                  }}
                >
                  Learn about this pattern
                </button>
              </p>
            )}
            <p className="meta">Trend: {ex.trend}</p>
            <p className="monitor-msg">{ex.message}</p>
            {ex.validation_note && (
              <div className="validation-note">
                <p>{ex.validation_note}</p>
              </div>
            )}
            {renderConfluence(ex)}
          </div>
        </div>
      );
    }

    return (
      <div className="card no-trade">
        <div className="card-header">
          <span className="status-badge" style={{ background: statusColor(ex.status) }}>
            {ex.status}
          </span>
          <span className="score">{ex.score}/100</span>
        </div>
        <div className="card-body">
          <p className="price">Close: {ex.close.toFixed(2)}</p>
          {ex.pattern && (
            <p className="pattern-line">
              <strong>{ex.pattern}</strong> ({ex.pattern_direction})
              <button
                className="learn-link"
                onClick={() => {
                  setView('learn');
                  openLearnTopic(ex.pattern as string);
                }}
              >
                Learn about this pattern
              </button>
            </p>
          )}
          {ex.chart_pattern && (
            <p className="pattern-line chart-pattern-line">
              <strong>Chart pattern: {ex.chart_pattern}</strong> ({ex.chart_pattern_direction})
              <button
                className="learn-link"
                onClick={() => {
                  setView('learn');
                  openLearnTopic(ex.chart_pattern as string);
                }}
              >
                Learn about this pattern
              </button>
            </p>
          )}
          <p className="no-trade-msg">No trade — insufficient confirmation</p>
          {renderConfluence(ex)}
        </div>
      </div>
    );
  };

  const renderHistory = () => {
    if (!data) return null;
    const rows = data.history.slice(-10).reverse();
    return (
      <div className="history">
        <h3>Recent context</h3>
        <table>
          <thead>
            <tr>
              <th>Time</th>
              <th>Close</th>
              <th>Score</th>
              <th>Status</th>
              <th>Direction</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r, i) => (
              <tr key={i}>
                <td>{new Date(r.timestamp).toLocaleString()}</td>
                <td>{r.close.toFixed(2)}</td>
                <td>{r.score}</td>
                <td>
                  <span className="mini-badge" style={{ background: statusColor(r.status) }}>
                    {r.status}
                  </span>
                </td>
                <td>{r.direction ?? '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    );
  };

  const cleanText = (s: string | null) =>
    s ? s.replace(/<[^>]+>/g, '').replace(/\s+/g, ' ').trim() : '';

  const renderNews = () => {
    if (!data || !data.news || data.news.length === 0) return null;
    return (
      <div className="news">
        <h3>News {data.ticker ? `· ${data.ticker}` : ''}</h3>
        <ul>
          {data.news.map((n, i) => (
            <li key={i}>
              <a href={n.link ?? '#'} target="_blank" rel="noopener noreferrer">
                {n.title ?? '(untitled)'}
              </a>
              {cleanText(n.summary) && (
                <p className="news-summary">{cleanText(n.summary)}</p>
              )}
            </li>
          ))}
        </ul>
      </div>
    );
  };

  const renderScanner = () => {
    return (
      <div className="scanner">
        <div className="scanner-head">
          <h3>Live Scanner — ranked by setup score</h3>
          <button onClick={fetchScan} disabled={scanLoading}>
            {scanLoading ? 'Scanning…' : 'Rescan'}
          </button>
        </div>
        {scanError && !scanLoading && <div className="error">Error: {scanError}</div>}
        {!scanData && !scanLoading && (
          <div className="scanner-empty">Press Rescan to load today's best setups.</div>
        )}
        {scanData && scanData.length === 0 && (
          <div className="scanner-empty">No tickers returned a usable setup.</div>
        )}
        {scanData && scanData.length > 0 && (
          <table className="scan-table">
            <thead>
              <tr>
                <th>Ticker</th>
                <th>Close</th>
                <th>Status</th>
                <th>Score</th>
                <th>Pattern</th>
                <th>Confidence</th>
              </tr>
            </thead>
            <tbody>
              {scanData.map((r) => (
                <tr key={r.ticker} className="scan-row" onClick={() => openTicker(r.ticker)}>
                  <td>{r.ticker}</td>
                  <td>
                    {r.currency?.symbol ?? ''}
                    {r.close != null ? r.close.toFixed(2) : '—'}{' '}
                    <span className="muted">{r.currency?.code ?? ''}</span>
                  </td>
                  <td>
                    <span
                      className="mini-badge"
                      style={{ background: statusColor(r.status ?? '') }}
                    >
                      {r.status}
                    </span>
                  </td>
                  <td>{r.score != null ? r.score : '—'}</td>
                  <td>{r.pattern ?? '—'}</td>
                  <td>
                    {r.validated === 'provisional' && (
                      <span className="provisional-badge">Provisional</span>
                    )}
                    {r.validated === 'experimental' && (
                      <span className="experimental-badge">Experimental</span>
                    )}
                    {!r.validated && '—'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    );
  };

  const renderLearnMenu = () => {
    if (learnLoading) return <div className="learn-loading">Loading topics…</div>;
    if (learnError) return <div className="error">Error: {learnError}</div>;
    if (!learnTopics) return <div className="learn-empty">Press the Learn tab to load topics.</div>;
    const patterns = learnTopics.filter((t) => t.direction !== null);
    const concepts = learnTopics.filter((t) => t.direction === null);
    const renderGroup = (label: string, items: LearnTopic[]) => (
      <div className="learn-group">
        <h4>{label}</h4>
        <ul className="learn-list">
          {items.map((t) => (
            <li key={t.id} className="learn-list-item" onClick={() => openLearnTopic(t.id)}>
              <strong>{t.title}</strong>
              <span>{t.teaser}</span>
            </li>
          ))}
        </ul>
      </div>
    );
    return (
      <div className="learn-menu">
        {patterns.length > 0 && renderGroup('Candlestick Patterns', patterns)}
        {concepts.length > 0 && renderGroup('Core Concepts', concepts)}
      </div>
    );
  };

  const renderLearnDetail = () => {
    if (!selectedTopic) return null;
    const bulletList = (items: string[]) => (
      <ul>
        {items.map((item, i) => (
          <li key={i}>{item}</li>
        ))}
      </ul>
    );
    return (
      <div className="learn-detail">
        <button className="learn-back" onClick={() => setSelectedTopic(null)}>
          ← Back to topics
        </button>
        <h3>{selectedTopic.title}</h3>
        <p className="learn-teaser">{selectedTopic.teaser}</p>
        <div className="learn-section">
          <h4>What is it</h4>
          <p>{selectedTopic.what_is_it}</p>
        </div>
        <div className="learn-section">
          <h4>Why it matters</h4>
          <p>{selectedTopic.why_it_matters}</p>
        </div>
        <div className="learn-section">
          <h4>How is it detected</h4>
          <p>{selectedTopic.how_is_it_detected}</p>
        </div>
        <div className="learn-section">
          <h4>Confirmation</h4>
          {bulletList(selectedTopic.confirmation)}
        </div>
        <div className="learn-section">
          <h4>Invalidation</h4>
          {bulletList(selectedTopic.invalidation)}
        </div>
      </div>
    );
  };

  const presetValue = TICKERS.some((t) => t.ticker === ticker) ? ticker : '';

  const positionReturnColor = (returnPct: number, status: string) => {
    if (status === 'closed' && returnPct >= 0) return '#16a34a';
    if (status === 'closed' && returnPct < 0) return '#dc2626';
    if (returnPct >= 0) return '#16a34a';
    if (returnPct > -3) return '#ca8a04';
    return '#dc2626';
  };

  const alignmentColor = (alignment: string | null) => {
    if (alignment === 'strong_bullish') return '#16a34a';
    if (alignment === 'strong_bearish') return '#dc2626';
    if (alignment === 'conflicting' || alignment === 'mixed') return '#ca8a04';
    if (alignment === 'aligned_bullish') return '#0d9488';
    if (alignment === 'aligned_bearish') return '#9333ea';
    return '#6b7280';
  };

  const renderAlignment = () => {
    if (!data?.alignment) return null;
    const a = data.alignment;
    return (
      <div className="alignment-card">
        <h3>Timeframe Alignment</h3>
        <div className="alignment-trends">
          <div><span>Daily</span><strong>{a.daily_trend ?? '—'}</strong></div>
          <div><span>1h</span><strong>{a.hourly_trend ?? '—'}</strong></div>
          <div><span>15m</span><strong>{a.m15_trend ?? '—'}</strong></div>
        </div>
        <div className="alignment-badge" style={{ background: alignmentColor(a.alignment) }}>
          {a.alignment ? a.alignment.replace(/_/g, ' ').toUpperCase() : 'N/A'}
        </div>
        <p className="alignment-note">{a.note}</p>
      </div>
    );
  };

  const renderPositions = () => {
    if (positionsLoading) return <div className="positions-loading">Loading positions…</div>;
    if (positionsError) return <div className="error">Error: {positionsError}</div>;
    if (!positions) return <div className="positions-empty">Press the Positions tab to load.</div>;

    const openPositions = positions.filter((p) => p.status === 'open');
    const closedPositions = positions.filter((p) => p.status === 'closed');

    const renderCard = (pos: Position) => (
      <div key={pos.id} className={`position-card ${pos.status}`}>
        <div className="position-header">
          <strong>{pos.ticker}</strong>
          <span className="mini-badge" style={{ background: statusColor(pos.status.toUpperCase()) }}>
            {pos.status.toUpperCase()}
          </span>
        </div>
        <div className="position-body">
          <div><span>Direction</span><strong>{pos.direction}</strong></div>
          <div><span>Entry price</span><strong>{pos.entry_price.toFixed(2)}</strong></div>
          <div><span>Target</span><strong>{pos.target1?.toFixed(2) ?? '—'}</strong></div>
          <div><span>Invalidation</span><strong>{pos.invalidation?.toFixed(2) ?? '—'}</strong></div>
          <div>
            <span>Return</span>
            <strong style={{ color: positionReturnColor(pos.display_return_pct, pos.status) }}>
              {pos.display_return_pct >= 0 ? '+' : ''}{pos.display_return_pct.toFixed(2)}%
            </strong>
          </div>
          {pos.status === 'open' && (
            <button className="close-btn" onClick={() => closePosition(pos.id)}>
              Close position
            </button>
          )}
          {pos.status === 'closed' && (
            <>
              <div><span>Exit price</span><strong>{pos.exit_price?.toFixed(2) ?? '—'}</strong></div>
              <div><span>Exit reason</span><strong>{pos.exit_reason}</strong></div>
              <div><span>Return</span><strong style={{ color: positionReturnColor(pos.return_pct ?? 0, 'closed') }}>
                {pos.return_pct != null ? (pos.return_pct >= 0 ? '+' : '') + pos.return_pct.toFixed(2) + '%' : '—'}
              </strong></div>
            </>
          )}
        </div>
      </div>
    );

    return (
      <div className="positions">
        <div className="paper-warning">
          This is simulated/paper trading only. No real money is at risk.
        </div>
        {openPositions.length > 0 && (
          <div className="positions-group">
            <h3>Open positions ({openPositions.length})</h3>
            <div className="positions-grid">
              {openPositions.map(renderCard)}
            </div>
          </div>
        )}
        {closedPositions.length > 0 && (
          <div className="positions-group">
            <h3>Closed positions ({closedPositions.length})</h3>
            <div className="positions-grid">
              {closedPositions.map(renderCard)}
            </div>
          </div>
        )}
        {positions.length === 0 && (
          <div className="positions-empty">No positions yet. Open a trade from the Single-stock view.</div>
        )}
      </div>
    );
  };

  const renderLive = () => {
    if (growwLoading && !growwStatus) return <div className="positions-loading">Connecting to Groww…</div>;
    if (growwError) return <div className="error">Error: {growwError}</div>;

    const connected = growwStatus?.connected ?? false;

    return (
      <div className="live">
        <div className="live-header">
          <h2>Live Trading — Groww</h2>
          <span className={`status-badge ${connected ? 'ok' : 'off'}`}>
            {connected ? 'Connected' : 'Disconnected'}
          </span>
          {connected && (
            <button className="refresh-btn" onClick={() => {
              fetchGrowwStatus();
              fetchGrowwHoldings();
              fetchGrowwPositions();
              fetchGrowwMargin();
              fetchGrowwOrders();
            }}>
              Refresh
            </button>
          )}
        </div>

        {!connected && growwStatus?.detail && (
          <div className="live-setup">
            <h3>Connection failed</h3>
            <p className="paper-warning">{growwStatus.detail}</p>
            <p>
              Credentials are present in the backend <code>.env</code> file, but Groww rejected them. This
              usually means the API key/secret pair doesn't have trading permissions enabled, has expired, or
              was regenerated — check this in Groww's own API key settings, not in this app.
            </p>
          </div>
        )}

        {!connected && !growwStatus?.detail && (
          <div className="live-setup">
            <h3>Setup</h3>
            <p>To enable live trading, add your Groww API credentials to the backend <code>.env</code> file:</p>
            <pre>{`GROWW_API_KEY=your_key\nGROWW_API_SECRET=your_secret\nGROWW_ALLOW_REAL_ORDERS=false`}</pre>
            <p className="paper-warning">Keep <code>GROWW_ALLOW_REAL_ORDERS=false</code> for safe mode. Real orders are disabled until you explicitly enable it.</p>
          </div>
        )}

        {connected && (
          <>
            <div className="live-grid">
              <div className="card">
                <h3>Margin</h3>
                {growwMargin ? (
                  <div className="kv">
                    <div><span>Available cash</span><strong>{growwMargin.available_cash?.toFixed(2) ?? '—'}</strong></div>
                    <div><span>Used margin</span><strong>{growwMargin.used_margin?.toFixed(2) ?? '—'}</strong></div>
                    <div><span>Available margin</span><strong>{growwMargin.available_margin?.toFixed(2) ?? '—'}</strong></div>
                  </div>
                ) : (
                  <button onClick={fetchGrowwMargin}>Load margin</button>
                )}
              </div>

              <div className="card">
                <h3>Holdings ({growwHoldings?.length ?? 0})</h3>
                <button onClick={fetchGrowwHoldings}>Refresh holdings</button>
                {growwHoldings && growwHoldings.length === 0 && <p className="empty">No holdings.</p>}
                {growwHoldings && growwHoldings.length > 0 && (
                  <div className="table-wrap">
                    <table>
                      <thead>
                        <tr><th>Symbol</th><th>Qty</th><th>Avg price</th><th>Invested</th></tr>
                      </thead>
                      <tbody>
                        {growwHoldings.map((h, i) => (
                          <tr key={i}>
                            <td>{h.trading_symbol}</td>
                            <td>{h.quantity}</td>
                            <td>{h.average_price?.toFixed(2)}</td>
                            <td>{h.invested_value?.toFixed(2)}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </div>

              <div className="card">
                <h3>Positions ({growwPositions?.length ?? 0})</h3>
                <button onClick={fetchGrowwPositions}>Refresh positions</button>
                {growwPositions && growwPositions.length === 0 && <p className="empty">No open positions.</p>}
                {growwPositions && growwPositions.length > 0 && (
                  <div className="table-wrap">
                    <table>
                      <thead>
                        <tr><th>Symbol</th><th>Qty</th><th>Avg price</th><th>Product</th></tr>
                      </thead>
                      <tbody>
                        {growwPositions.map((p, i) => (
                          <tr key={i}>
                            <td>{p.trading_symbol}</td>
                            <td>{p.quantity}</td>
                            <td>{p.average_price?.toFixed(2)}</td>
                            <td>{p.product}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </div>
            </div>

            <div className="card order-card">
              <h3>Place Order</h3>
              <div className="order-form">
                <input value={orderForm.trading_symbol} onChange={e => setOrderForm({ ...orderForm, trading_symbol: e.target.value })} placeholder="Symbol" />
                <select value={orderForm.exchange} onChange={e => setOrderForm({ ...orderForm, exchange: e.target.value })}>
                  <option value="NSE">NSE</option>
                  <option value="BSE">BSE</option>
                </select>
                <select value={orderForm.product} onChange={e => setOrderForm({ ...orderForm, product: e.target.value })}>
                  <option value="CNC">CNC</option>
                  <option value="MIS">MIS</option>
                  <option value="NRML">NRML</option>
                </select>
                <select value={orderForm.transaction_type} onChange={e => setOrderForm({ ...orderForm, transaction_type: e.target.value })}>
                  <option value="BUY">BUY</option>
                  <option value="SELL">SELL</option>
                </select>
                <input type="number" value={orderForm.quantity} onChange={e => setOrderForm({ ...orderForm, quantity: Number(e.target.value) })} placeholder="Qty" min={1} />
                <input value={orderForm.price} onChange={e => setOrderForm({ ...orderForm, price: e.target.value })} placeholder="Price" />
                <button className="primary" onClick={placeGrowwOrder} disabled={growwLoading}>Place order</button>
              </div>
            </div>

            <div className="card">
              <h3>Orders</h3>
              <button onClick={fetchGrowwOrders}>Refresh orders</button>
              {growwOrders && growwOrders.length === 0 && <p className="empty">No orders.</p>}
              {growwOrders && growwOrders.length > 0 && (
                <div className="table-wrap">
                  <table>
                    <thead>
                      <tr><th>ID</th><th>Symbol</th><th>Side</th><th>Qty</th><th>Status</th><th>Placed</th></tr>
                    </thead>
                    <tbody>
                      {growwOrders.map((o, i) => (
                        <tr key={i}>
                          <td>{o.order_id}</td>
                          <td>{o.trading_symbol}</td>
                          <td>{o.transaction_type}</td>
                          <td>{o.quantity}</td>
                          <td>{o.status}</td>
                          <td>{o.placed_at ? new Date(o.placed_at).toLocaleString() : '—'}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          </>
        )}
      </div>
    );
  };

  const eventBadgeColor = (type: string) => {
    if (type === 'buy') return '#16a34a';
    if (type === 'sell') return '#7c3aed';
    if (type === 'error') return '#dc2626';
    if (type === 'skip') return '#ca8a04';
    return '#6b7280'; // observe
  };

  const parseReason = (reason: string | null) => {
    if (!reason) return { structural: null as string | null, rest: '' };
    const m = reason.match(/structural=([^/,]+)\/([^,]+)/);
    return { structural: m ? `${m[1]}/${m[2]}` : null, rest: reason };
  };

  const probBar = (label: string, value: number | null, color: string) => (
    <div className="prob-row">
      <span className="prob-label">{label}</span>
      <div className="prob-track">
        <div
          className="prob-fill"
          style={{ width: `${Math.round((value ?? 0) * 100)}%`, background: color }}
        />
      </div>
      <span className="prob-value">{value != null ? `${Math.round(value * 100)}%` : '—'}</span>
    </div>
  );

  const renderAutonomous = () => {
    if (autoLoading && !autoStatus) return <div className="positions-loading">Loading autonomous status…</div>;

    const latestByTicker = new Map<string, AutonomousEvent>();
    (autoEvents ?? []).forEach((e) => {
      if (e.event_type !== 'observe') return;
      const existing = latestByTicker.get(e.ticker);
      if (!existing || e.id > existing.id) latestByTicker.set(e.ticker, e);
    });

    const killActive = autoStatus?.risk_state.kill_switch_active ?? false;
    const pnl = autoStatus?.risk_state.realized_pnl_today ?? 0;

    return (
      <div className="autonomous">
        {autoError && <div className="error">Error: {autoError}</div>}

        {autoStatus && (
          <div className="auto-summary">
            <div className={`auto-summary-card mode-${autoStatus.mode}`}>
              <span>Mode</span>
              <strong>{autoStatus.mode === 'live' ? '🔴 LIVE (real orders)' : '🧪 PAPER (simulated)'}</strong>
            </div>
            <div className={`auto-summary-card ${killActive ? 'kill-active' : 'kill-ok'}`}>
              <span>Kill switch</span>
              <strong>{killActive ? 'ACTIVE — trading halted' : 'OK'}</strong>
              {killActive && (
                <button className="reset-btn" onClick={resetKillSwitch} disabled={killSwitchResetting}>
                  {killSwitchResetting ? 'Resetting…' : 'Reset kill switch'}
                </button>
              )}
              {killActive && autoStatus.risk_state.kill_switch_reason && (
                <p className="kill-reason">{autoStatus.risk_state.kill_switch_reason}</p>
              )}
            </div>
            <div className="auto-summary-card">
              <span>Realized P&amp;L today</span>
              <strong style={{ color: pnl >= 0 ? '#16a34a' : '#dc2626' }}>
                {pnl >= 0 ? '+' : ''}{pnl.toFixed(2)}
              </strong>
            </div>
            <div className="auto-summary-card">
              <span>Open positions</span>
              <strong>{autoStatus.open_autonomous_positions} / {autoStatus.risk_limits.max_open_positions}</strong>
            </div>
            <div className="auto-summary-card">
              <span>Thresholds</span>
              <strong>buy ≥ {Math.round(autoStatus.buy_probability_threshold * 100)}% · sell ≥ {Math.round(autoStatus.sell_probability_threshold * 100)}%</strong>
            </div>
          </div>
        )}

        <h3>Watchlist — live observations</h3>
        {!autoEvents && <div className="autonomous-empty">Waiting for the autonomous loop to report in…</div>}
        {autoEvents && latestByTicker.size === 0 && (
          <div className="autonomous-empty">
            No observations yet. Is <code>python -m backend.autonomous.trader</code> running, and is the market open?
          </div>
        )}
        {latestByTicker.size > 0 && (
          <div className="watchlist-grid">
            {(autoStatus?.watchlist ?? Array.from(latestByTicker.keys())).map((ticker) => {
              const obs = latestByTicker.get(ticker);
              const { structural } = parseReason(obs?.reason ?? null);
              return (
                <div key={ticker} className="watchlist-card">
                  <div className="watchlist-card-header">
                    <strong>{ticker}</strong>
                    <span className="watchlist-price">{obs?.price != null ? obs.price.toFixed(2) : '—'}</span>
                  </div>
                  {structural && <div className="watchlist-structural">{structural}</div>}
                  {obs ? (
                    <>
                      {probBar('Buy', obs.buy_probability, '#16a34a')}
                      {probBar('Sell', obs.sell_probability, '#dc2626')}
                      <div className="watchlist-updated">
                        updated {new Date(obs.ts).toLocaleTimeString()}
                      </div>
                    </>
                  ) : (
                    <div className="watchlist-updated">no data yet</div>
                  )}
                </div>
              );
            })}
          </div>
        )}

        <h3>Decision log</h3>
        {autoEvents && autoEvents.length === 0 && (
          <div className="autonomous-empty">No events logged yet.</div>
        )}
        {autoEvents && autoEvents.length > 0 && (
          <div className="table-wrap">
            <table className="event-log">
              <thead>
                <tr>
                  <th>Time</th>
                  <th>Ticker</th>
                  <th>Event</th>
                  <th>Price</th>
                  <th>Qty</th>
                  <th>Buy%</th>
                  <th>Sell%</th>
                  <th>Mode</th>
                  <th>Reason</th>
                </tr>
              </thead>
              <tbody>
                {autoEvents
                  .filter((e) => e.event_type !== 'observe')
                  .concat(autoEvents.filter((e) => e.event_type === 'observe').slice(0, 20))
                  .sort((a, b) => b.id - a.id)
                  .slice(0, 60)
                  .map((e) => (
                    <tr key={e.id}>
                      <td>{new Date(e.ts).toLocaleTimeString()}</td>
                      <td>{e.ticker}</td>
                      <td>
                        <span className="mini-badge" style={{ background: eventBadgeColor(e.event_type) }}>
                          {e.event_type}
                        </span>
                      </td>
                      <td>{e.price != null ? e.price.toFixed(2) : '—'}</td>
                      <td>{e.quantity ?? '—'}</td>
                      <td>{e.buy_probability != null ? Math.round(e.buy_probability * 100) : '—'}</td>
                      <td>{e.sell_probability != null ? Math.round(e.sell_probability * 100) : '—'}</td>
                      <td>{e.mode}</td>
                      <td className="event-reason">{e.reason ?? '—'}</td>
                    </tr>
                  ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    );
  };

  return (
    <div className="app">
      <header className="topbar">
        <h1>Trade Assistant</h1>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
          <div className="view-toggle">
            <button
              className={view === 'single' ? 'tab active' : 'tab'}
              onClick={() => setView('single')}
            >
              Single
            </button>
            <button
              className={view === 'scanner' ? 'tab active' : 'tab'}
              onClick={() => {
                setView('scanner');
                fetchScan();
              }}
            >
              Scanner
            </button>
            <button
              className={view === 'learn' ? 'tab active' : 'tab'}
              onClick={() => {
                setView('learn');
                if (!learnTopics && !learnLoading) {
                  fetchLearnTopics();
                }
              }}
            >
              Learn
            </button>
            <button
              className={view === 'positions' ? 'tab active' : 'tab'}
              onClick={() => {
                setView('positions');
                if (!positions && !positionsLoading) {
                  fetchPositions();
                }
              }}
            >
              Positions
            </button>
            <button
              className={view === 'live' ? 'tab active' : 'tab'}
              onClick={() => {
                setView('live');
                if (!growwStatus && !growwLoading) {
                  fetchGrowwStatus();
                }
              }}
            >
              Live
            </button>
            <button
              className={view === 'autonomous' ? 'tab active' : 'tab'}
              onClick={() => setView('autonomous')}
            >
              Autonomous
            </button>
          </div>

          {view === 'single' && (
            <>
              <select
                value={source}
                onChange={(e) => setSource(e.target.value)}
                style={{ padding: '6px 8px', borderRadius: 6, border: '1px solid var(--border)' }}
              >
                <option value="synthetic">Synthetic</option>
                <option value="yfinance">yfinance</option>
              </select>
              {source === 'yfinance' && (
                <>
                  <select
                    value={presetValue}
                    onChange={(e) => setTicker(e.target.value)}
                    style={{ padding: '6px 8px', borderRadius: 6, border: '1px solid var(--border)', maxWidth: 180 }}
                    title="Preset tickers"
                  >
                    <option value="" disabled>
                      Market ▾
                    </option>
                    {TICKERS.map((t) => (
                      <option key={t.ticker} value={t.ticker}>
                        {t.flag} {t.market} — {t.ticker}
                      </option>
                    ))}
                  </select>
                  <input
                    value={ticker}
                    onChange={(e) => setTicker(e.target.value)}
                    placeholder="Ticker"
                    style={{ padding: '6px 8px', borderRadius: 6, border: '1px solid var(--border)', width: 110 }}
                  />
                  <select
                    value={intervalVal}
                    onChange={(e) => setIntervalVal(e.target.value)}
                    style={{ padding: '6px 8px', borderRadius: 6, border: '1px solid var(--border)' }}
                  >
                    <option value="1m">1m</option>
                  </select>
                </>
              )}
              <button onClick={() => fetchData()} disabled={loading}>
                {loading ? 'Refreshing…' : 'Refresh'}
              </button>
            </>
          )}
        </div>
      </header>

      {error && !stale && <div className="error">Error: {error}</div>}
      {error && stale && (
        <div className="stale-note">
          Showing last loaded data — refresh failed: {error}
        </div>
      )}

      {view === 'scanner' ? (
        renderScanner()
      ) : view === 'learn' ? (
        <div className="learn">
          {selectedTopic ? renderLearnDetail() : renderLearnMenu()}
        </div>
      ) : view === 'positions' ? (
        renderPositions()
      ) : view === 'live' ? (
        renderLive()
      ) : view === 'autonomous' ? (
        renderAutonomous()
      ) : (
        <div className="layout">
          <div className="main-col">
            {renderExplanation()}

            {data?.chart && (
              <CandleChart candles={data.chart.candles} levels={data.chart.levels} />
            )}

            {renderHistory()}
            {renderAlignment()}
          </div>

          <aside className="news-col">{renderNews()}</aside>
        </div>
      )}
    </div>
  );
}

export default App;
