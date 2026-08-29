import { useEffect, useRef, useState } from 'react';
import type { AnalyzeResponse } from './types';
import CandleChart from './CandleChart';
import './App.css';

const API_URL = 'http://127.0.0.1:8000/analyze/synthetic';

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
  const [intervalVal, setIntervalVal] = useState(() => readLS(LS.interval, '1d'));

  const fetched = useRef(false);

  const fetchData = async () => {
    setLoading(true);
    setError(null);
    setStale(false);
    try {
      const params = new URLSearchParams();
      if (source === 'yfinance') {
        params.set('ticker', ticker);
        params.set('interval', intervalVal);
      }
      const url =
        source === 'synthetic'
          ? API_URL
          : 'http://127.0.0.1:8000/analyze/' + source + '?' + params.toString();
      const res = await fetch(url);
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
            {ex.pattern_direction === 'bearish' && (
              <span className="experimental-badge">Experimental</span>
            )}
          </div>
          <div className="card-body">
            <p className="price">Close: {fmtPrice(ex.close)}</p>
            <p className="pattern-line">
              <strong>{ex.pattern || '—'}</strong> ({ex.pattern_direction})
            </p>
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
            {ex.pattern_direction === 'bearish' && (
              <span className="experimental-badge">Experimental</span>
            )}
          </div>
          <div className="card-body">
            <p className="price">Close: {fmtPrice(ex.close)}</p>
            <p className="meta">Trend: {ex.trend}</p>
            <p className="monitor-msg">{ex.message}</p>
            {ex.validation_note && (
              <div className="validation-note">
                <p>{ex.validation_note}</p>
              </div>
            )}
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
          <p className="no-trade-msg">No trade — insufficient confirmation</p>
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

  const presetValue = TICKERS.some((t) => t.ticker === ticker) ? ticker : '';

  return (
    <div className="app">
      <header className="topbar">
        <h1>Trade Assistant</h1>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
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
                <option value="15m">15m</option>
                <option value="1h">1h</option>
                <option value="1d">1d</option>
              </select>
            </>
          )}
          <button onClick={fetchData} disabled={loading}>
            {loading ? 'Refreshing…' : 'Refresh'}
          </button>
        </div>
      </header>

      {error && !stale && <div className="error">Error: {error}</div>}
      {error && stale && (
        <div className="stale-note">
          Showing last loaded data — refresh failed: {error}
        </div>
      )}

      <div className="layout">
        <div className="main-col">
          {renderExplanation()}

          {data?.chart && (
            <CandleChart candles={data.chart.candles} levels={data.chart.levels} />
          )}

          {renderHistory()}
        </div>

        <aside className="news-col">{renderNews()}</aside>
      </div>
    </div>
  );
}

export default App;
