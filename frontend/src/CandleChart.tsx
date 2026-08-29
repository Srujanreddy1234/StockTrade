import type { Candle, ChartLevels } from './types';

interface Props {
  candles: Candle[];
  levels: ChartLevels;
}

const W = 720;
const H = 380;
const PAD_X = 6;
const PRICE_TOP = 12;
const PRICE_BOTTOM = 300;
const VOL_TOP = 312;
const VOL_BOTTOM = 372;

interface LevelDef {
  value: number | null;
  label: string;
  color: string;
}

export default function CandleChart({ candles, levels }: Props) {
  if (!candles || candles.length === 0) {
    return <div className="chart-empty">No candle data</div>;
  }

  const levelDefs: LevelDef[] = [
    { value: levels.entry_zone_low, label: 'Entry', color: '#2563eb' },
    { value: levels.entry_zone_high, label: 'Entry', color: '#2563eb' },
    { value: levels.target1, label: 'Target 1', color: '#16a34a' },
    { value: levels.target2, label: 'Target 2', color: '#15803d' },
    { value: levels.invalidation, label: 'Invalidation', color: '#dc2626' },
    { value: levels.support, label: 'Support', color: '#0891b2' },
    { value: levels.resistance, label: 'Resistance', color: '#d97706' },
  ].filter((l) => l.value != null) as LevelDef[];

  const prices = candles.flatMap((c) => [c.high, c.low]);
  const allVals = [...prices, ...levelDefs.map((l) => l.value as number)];
  let minP = Math.min(...allVals);
  let maxP = Math.max(...allVals);
  const pad = (maxP - minP) * 0.04 || 1;
  minP -= pad;
  maxP += pad;

  const maxVol = Math.max(...candles.map((c) => c.volume), 1);
  const n = candles.length;
  const slot = (W - PAD_X * 2) / n;
  const bodyW = Math.max(1.5, slot * 0.6);

  const priceY = (p: number) =>
    PRICE_TOP + (1 - (p - minP) / (maxP - minP)) * (PRICE_BOTTOM - PRICE_TOP);
  const volY = (v: number) =>
    VOL_BOTTOM - (v / maxVol) * (VOL_BOTTOM - VOL_TOP);
  const xCenter = (i: number) => PAD_X + slot * i + slot / 2;

  const emaPoints = candles
    .map((c, i) => (c.ema != null ? `${xCenter(i)},${priceY(c.ema)}` : null))
    .filter((p): p is string => p != null);
  const emaPath = emaPoints.length > 1 ? 'M ' + emaPoints.join(' L ') : '';

  return (
    <div className="chart-card">
      <h3>Price &amp; Volume</h3>
      <svg
        className="candle-svg"
        viewBox={`0 0 ${W} ${H}`}
        preserveAspectRatio="none"
        role="img"
        aria-label="Candlestick chart"
      >
        {/* gridlines */}
        {[0, 0.25, 0.5, 0.75, 1].map((t) => {
          const p = minP + (maxP - minP) * (1 - t);
          const y = priceY(p);
          return (
            <g key={t}>
              <line x1={0} y1={y} x2={W} y2={y} stroke="#eef2f7" />
              <text x={W - 4} y={y - 2} className="axis-label" textAnchor="end">
                {p.toFixed(2)}
              </text>
            </g>
          );
        })}

        {/* level lines */}
        {levelDefs.map((l, i) => {
          const y = priceY(l.value as number);
          return (
            <g key={i}>
              <line
                x1={0}
                y1={y}
                x2={W}
                y2={y}
                stroke={l.color}
                strokeWidth={1}
                strokeDasharray="5 4"
                opacity={0.8}
              />
              <text x={4} y={y - 3} fill={l.color} className="level-label">
                {l.label} {l.value?.toFixed(2)}
              </text>
            </g>
          );
        })}

        {/* candles + volume */}
        {candles.map((c, i) => {
          const x = xCenter(i);
          const bull = c.close >= c.open;
          const color = bull ? '#16a34a' : '#dc2626';
          const yHigh = priceY(c.high);
          const yLow = priceY(c.low);
          const yOpen = priceY(c.open);
          const yClose = priceY(c.close);
          const top = Math.min(yOpen, yClose);
          const height = Math.max(1, Math.abs(yClose - yOpen));
          return (
            <g key={i}>
              <line x1={x} y1={yHigh} x2={x} y2={yLow} stroke={color} strokeWidth={1} />
              <rect
                x={x - bodyW / 2}
                y={top}
                width={bodyW}
                height={height}
                fill={color}
              />
              <rect
                x={x - bodyW / 3}
                y={volY(c.volume)}
                width={(bodyW * 2) / 3}
                height={VOL_BOTTOM - volY(c.volume)}
                fill={bull ? '#bbf7d0' : '#fecaca'}
              />
            </g>
          );
        })}

        {/* EMA overlay */}
        {emaPath && (
          <path d={emaPath} fill="none" stroke="#7c3aed" strokeWidth={1.5} opacity={0.9} />
        )}
      </svg>
      <div className="chart-legend">
        <span><i className="swatch bull" /> Bullish</span>
        <span><i className="swatch bear" /> Bearish</span>
        <span><i className="swatch ema" /> EMA 20</span>
      </div>
    </div>
  );
}
