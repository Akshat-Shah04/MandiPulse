"use client";

import { useState } from "react";
import { kg, niceDate, type ForecastPoint, type SeriesPoint } from "@/lib/api";

// Chart size in SVG units. The SVG stretches to the panel width.
const WIDTH = 360;
const HEIGHT = 160;
const LEFT = 52; // room for price labels
const RIGHT = 8;
const TOP = 12;
const BOTTOM = 22; // room for date labels

// With a forecast we only show the recent past, so the next 14 days are readable.
const HISTORY_DAYS_WITH_FORECAST = 120;

type Props = {
  points: SeriesPoint[];
  forecast?: ForecastPoint[];
};

function toTime(date: string) {
  return new Date(date).getTime();
}

export default function TrendChart({ points, forecast = [] }: Props) {
  const [hoverDate, setHoverDate] = useState<string | null>(null);

  if (points.length < 2) {
    return <div className="grid h-[160px] place-items-center hud-label">Loading history…</div>;
  }

  // ---- 1. which history points to draw
  let history = points;
  if (forecast.length > 0) {
    const cutoff = toTime(points[points.length - 1].date) - HISTORY_DAYS_WITH_FORECAST * 86_400_000;
    history = points.filter((p) => toTime(p.date) >= cutoff);
  }

  // ---- 2. the range of dates and prices the chart must fit
  const firstTime = toTime(history[0].date);
  const lastTime = forecast.length > 0 ? toTime(forecast[forecast.length - 1].date) : toTime(history[history.length - 1].date);

  const allPrices = [
    ...history.map((p) => p.median_modal),
    ...forecast.map((f) => f.lo),
    ...forecast.map((f) => f.hi),
  ];
  let lowPrice = Math.min(...allPrices);
  let highPrice = Math.max(...allPrices);
  const padding = (highPrice - lowPrice) * 0.1 || 50;
  lowPrice -= padding;
  highPrice += padding;

  // ---- 3. helpers that turn a date / price into an x / y position
  function x(date: string) {
    return LEFT + ((toTime(date) - firstTime) / (lastTime - firstTime || 1)) * (WIDTH - LEFT - RIGHT);
  }
  function y(price: number) {
    return TOP + (1 - (price - lowPrice) / (highPrice - lowPrice)) * (HEIGHT - TOP - BOTTOM);
  }

  // ---- 4. build the SVG paths
  const historyLine = history.map((p, i) => `${i === 0 ? "M" : "L"}${x(p.date)},${y(p.median_modal)}`).join(" ");
  const historyArea = `${historyLine} L${x(history[history.length - 1].date)},${HEIGHT - BOTTOM} L${x(history[0].date)},${HEIGHT - BOTTOM} Z`;

  const lastReal = history[history.length - 1];
  let forecastLine = "";
  let forecastBand = "";
  if (forecast.length > 0) {
    // Start the dashed line at the last real price so the two lines join up.
    forecastLine = `M${x(lastReal.date)},${y(lastReal.median_modal)} ` + forecast.map((f) => `L${x(f.date)},${y(f.yhat)}`).join(" ");
    const top = forecast.map((f) => `${x(f.date)},${y(f.hi)}`);
    const bottom = forecast.map((f) => `${x(f.date)},${y(f.lo)}`).reverse();
    forecastBand = `M${x(lastReal.date)},${y(lastReal.median_modal)} L${top.join(" L")} L${bottom.join(" L")} Z`;
  }

  const priceTicks = [lowPrice + (highPrice - lowPrice) * 0.15, (lowPrice + highPrice) / 2, highPrice - (highPrice - lowPrice) * 0.15];

  // ---- 5. what to show under the mouse
  const hoverHistory = history.find((p) => p.date === hoverDate);
  const hoverForecast = forecast.find((f) => f.date === hoverDate);
  const hoverPrice = hoverHistory?.median_modal ?? hoverForecast?.yhat;

  function onMouseMove(event: React.MouseEvent<SVGSVGElement>) {
    const box = event.currentTarget.getBoundingClientRect();
    const mouseX = ((event.clientX - box.left) / box.width) * WIDTH;
    const candidates = [...history.map((p) => p.date), ...forecast.map((f) => f.date)];
    let closest = candidates[0];
    for (const d of candidates) {
      if (Math.abs(x(d) - mouseX) < Math.abs(x(closest) - mouseX)) closest = d;
    }
    setHoverDate(closest);
  }

  return (
    <div>
      <svg viewBox={`0 0 ${WIDTH} ${HEIGHT}`} className="w-full" onMouseMove={onMouseMove} onMouseLeave={() => setHoverDate(null)}>
        <defs>
          <linearGradient id="history-fill" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="rgba(64,224,255,0.30)" />
            <stop offset="100%" stopColor="rgba(64,224,255,0)" />
          </linearGradient>
        </defs>

        {priceTicks.map((price) => (
          <g key={price}>
            <line x1={LEFT} x2={WIDTH - RIGHT} y1={y(price)} y2={y(price)} stroke="rgba(64,224,255,0.1)" />
            <text x={LEFT - 4} y={y(price) + 4} textAnchor="end" fontSize={11} fill="var(--text-dim)">
              {kg(price)}
            </text>
          </g>
        ))}

        <path d={historyArea} fill="url(#history-fill)" />
        <path d={historyLine} fill="none" stroke="var(--cyan)" strokeWidth={1.5} />

        {forecast.length > 0 && (
          <>
            <path d={forecastBand} fill="rgba(255,179,64,0.18)" />
            <path d={forecastLine} fill="none" stroke="var(--amber)" strokeWidth={1.5} strokeDasharray="4 3" />
            <line x1={x(lastReal.date)} x2={x(lastReal.date)} y1={TOP} y2={HEIGHT - BOTTOM} stroke="rgba(255,179,64,0.4)" strokeDasharray="2 3" />
          </>
        )}

        <text x={LEFT} y={HEIGHT - 6} fontSize={11} fill="var(--text-dim)">{niceDate(history[0].date)}</text>
        <text x={WIDTH - RIGHT} y={HEIGHT - 6} fontSize={11} fill="var(--text-dim)" textAnchor="end">
          {niceDate(forecast.length > 0 ? forecast[forecast.length - 1].date : lastReal.date)}
        </text>

        {hoverDate && hoverPrice !== undefined && (
          <g>
            <line x1={x(hoverDate)} x2={x(hoverDate)} y1={TOP} y2={HEIGHT - BOTTOM} stroke="var(--text-dim)" strokeDasharray="2 2" />
            <circle cx={x(hoverDate)} cy={y(hoverPrice)} r={3} fill={hoverForecast ? "var(--amber)" : "var(--cyan)"} />
          </g>
        )}
      </svg>

      {/* Hover readout under the chart: easier to read than text inside the SVG */}
      <div className="h-5 text-[12px] text-hud-dim">
        {hoverDate && hoverHistory && `${niceDate(hoverDate)} · ${kg(hoverHistory.median_modal)}`}
        {hoverDate && hoverForecast &&
          `${niceDate(hoverDate)} · forecast ${kg(hoverForecast.yhat)} (likely ${kg(hoverForecast.lo)}–${kg(hoverForecast.hi)})`}
        {!hoverDate && forecast.length > 0 && (
          <span>
            <span className="text-hud-cyan">━</span> actual &nbsp; <span className="text-hud-amber">┅</span> forecast, shaded = 80% likely range
          </span>
        )}
      </div>
    </div>
  );
}
