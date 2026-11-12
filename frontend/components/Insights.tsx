"use client";

import { kg, modelLabel, niceDate, type ForecastResult, type MarketPrice, type ModelScores } from "@/lib/api";

/** One sentence: where the price is expected to be in 14 days. */
export function ForecastSummary({ forecast, todayPrice }: { forecast: ForecastResult | null; todayPrice: number | null }) {
  if (!forecast) return null;
  if (!forecast.available || forecast.points.length === 0) {
    return <p className="text-[13px] text-hud-dim">Not enough mandis here for a reliable forecast.</p>;
  }

  const last = forecast.points[forecast.points.length - 1];
  const change = todayPrice ? ((last.yhat - todayPrice) / todayPrice) * 100 : null;

  let direction = "about the same";
  let colour = "text-hud-text";
  if (change !== null && change > 2) {
    direction = `up about ${change.toFixed(0)}%`;
    colour = "text-hud-amber";
  } else if (change !== null && change < -2) {
    direction = `down about ${Math.abs(change).toFixed(0)}%`;
    colour = "text-hud-green";
  }

  return (
    <div className="text-[13px] leading-5">
      <span className="hud-label">Next 2 weeks</span>
      <div>
        By {niceDate(last.date)}: <span className={`font-semibold ${colour}`}>{kg(last.yhat)}</span>{" "}
        <span className={colour}>({direction})</span>
      </div>
      <div className="text-hud-dim">
        likely between {kg(last.lo)} and {kg(last.hi)}
      </div>
    </div>
  );
}

/** Mandis whose price today was flagged as unusual by the anomaly detector. */
export function UnusualPrices({ markets, onPick }: { markets: MarketPrice[]; onPick: (m: MarketPrice) => void }) {
  const flagged = markets.filter((m) => m.anomaly);
  return (
    <div>
      <p className="mb-2 text-[13px] leading-5 text-hud-dim">
        {flagged.length === 0
          ? "No unusual prices on this day."
          : `${flagged.length} mandi${flagged.length === 1 ? "" : "s"} reported a price far from their state and their own recent prices. Could be a data-entry error or a real local shock.`}
      </p>
      <ul className="grid gap-1">
        {flagged.slice(0, 6).map((m) => (
          <li key={m.id}>
            <button onClick={() => onPick(m)} className="w-full rounded-sm px-2 py-1 text-left hover:bg-hud-red/10">
              <div className="flex justify-between text-[14px]">
                <span>
                  <span className="text-hud-red">●</span> {m.market}
                </span>
                <span className="text-hud-red">{kg(m.modal)}</span>
              </div>
              <div className="text-[12px] text-hud-dim">{m.state} · {m.reason}</div>
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}

/** How accurate each model was when we tested it on past weeks. */
export function ModelAccuracy({ scores }: { scores: ModelScores | null }) {
  if (!scores || scores.models.length === 0) return <p className="text-[13px] text-hud-dim">Models not trained yet.</p>;
  return (
    <div className="text-[13px]">
      <p className="mb-2 leading-5 text-hud-dim">
        Average error when forecasting 1–14 days ahead on the last 6 weeks (lower is better). The forecast uses the
        best model for each day ahead.
      </p>
      <table className="w-full">
        <thead>
          <tr className="hud-label text-left">
            <th className="pb-1 font-normal">Model</th>
            <th className="pb-1 text-right font-normal">Error</th>
            <th className="pb-1 text-right font-normal">Used for</th>
          </tr>
        </thead>
        <tbody>
          {scores.models.map((m) => (
            <tr key={m.model} className={m.used_for_days.length ? "text-hud-text" : "text-hud-dim"}>
              <td className="py-0.5">{modelLabel[m.model] ?? m.model}</td>
              <td className="py-0.5 text-right tabular-nums">{m.mape.toFixed(1)}%</td>
              <td className="py-0.5 text-right">{daysText(m.used_for_days)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** [1,2,3,4,5,12,13] -> "days 1–5, 12–13" */
function daysText(days: number[]): string {
  if (days.length === 0) return "—";
  const ranges: string[] = [];
  let start = days[0];
  let previous = days[0];
  for (const d of [...days.slice(1), Infinity]) {
    if (d !== previous + 1) {
      ranges.push(start === previous ? `${start}` : `${start}–${previous}`);
      start = d;
    }
    previous = d;
  }
  return `day${days.length > 1 ? "s" : ""} ${ranges.join(", ")}`;
}
