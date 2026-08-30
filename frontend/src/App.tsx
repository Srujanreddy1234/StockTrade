import { useEffect, useRef, useState } from 'react';
import type { AnalyzeResponse, LearnTopic, ScanResult, ScanResponse } from './types';
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
  const [view, setView] = useState<'single' | 'scanner' | 'learn'>('single');
  const [scanData, setScanData] = useState<ScanResult[] | null>(null);
  const [scanLoading, setScanLoading] = useState(false);
  const [scanError, setScanError] = useState<string | null>(null);
  const [learnTopics, setLearnTopics] = useState<LearnTopic[] | null>(null);
  const [learnLoading, setLearnLoading] = useState(false);
  const [learnError, setLearnError] = useState<string | null>(null);
  const [selectedTopic, setSelectedTopic] = useState<LearnTopic | null>(null);

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
          ? API_URL
          : 'http://127.0.0.1:8000/analyze/' + src + '?' + params.toString();
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

  const fetchScan = async () => {
    setScanLoading(true);
    setScanError(null);
    try {
      const res = await fetch('http://127.0.0.1:8000/scan');
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
      const res = await fetch('http://127.0.0.1:8000/learn');
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
      const res = await fetch('http://127.0.0.1:8000/learn/' + topicId);
      if (!res.ok) throw new Error('HTTP ' + res.status);
      const json: LearnTopic = await res.json();
      setSelectedTopic(json);
    } catch (e: unknown) {
      setLearnError(e instanceof Error ? e.message : String(e));
    } finally {
      setLearnLoading(false);
    }
  };

  // Clicking a scanner row loads that ticker into the single-stock view.
  const openTicker = (t: string) => {
    setSource('yfinance');
    setTicker(t);
    setIntervalVal('1d');
    setView('single');
    fetchData({ source: 'yfinance', ticker: t, interval: '1d' });
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
              <strong>{ex.pattern || '—'}</strong> ({ex.pattern_direction})
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
                    <option value="15m">15m</option>
                    <option value="1h">1h</option>
                    <option value="1d">1d</option>
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
      ) : (
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
      )}
    </div>
  );
}

export default App;
