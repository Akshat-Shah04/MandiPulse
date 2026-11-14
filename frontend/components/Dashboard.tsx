"use client";

import { useEffect, useMemo, useState } from "react";
import GroupsPanel from "@/components/Groups";
import IndiaMap, { type ColourMode } from "@/components/IndiaMap";
import { ForecastSummary, ModelAccuracy, UnusualPrices } from "@/components/Insights";
import TrendChart from "@/components/TrendChart";
import {
  api,
  BINS,
  GROUP_COLOURS,
  binFor,
  kg,
  niceDate,
  pctChange,
  qtl,
  type CommoditySummary,
  type DayCount,
  type ForecastResult,
  type MarketGroups,
  type ModelScores,
  type MarketPrice,
  type PriceMap,
  type Series,
  type StatePrice,
  type Status,
} from "@/lib/api";

// ---------------------------------------------------------------- small parts

function Panel({ title, children, className = "" }: { title?: string; children: React.ReactNode; className?: string }) {
  return (
    <section className={`hud-panel shrink-0 p-4 ${className}`}>
      {title && <h2 className="hud-label mb-3 text-hud-cyan">{title}</h2>}
      {children}
    </section>
  );
}

function Kpi({ label, value, sub, tone = "text-hud-cyan" }: { label: string; value: string; sub?: string; tone?: string }) {
  return (
    <div className="hud-panel px-4 py-3">
      <div className="hud-label">{label}</div>
      <div className={`font-display text-[1.7rem] font-bold leading-tight ${tone}`}>{value}</div>
      {sub && <div className="truncate text-[13px] text-hud-dim">{sub}</div>}
    </div>
  );
}

function changeOver(points: { date: string; median_modal: number }[], days = 7): number | null {
  if (points.length < 2) return null;
  const last = points[points.length - 1];
  const target = new Date(last.date).getTime() - days * 864e5;
  const prev = [...points].reverse().find((p) => new Date(p.date).getTime() <= target);
  return prev ? ((last.median_modal - prev.median_modal) / prev.median_modal) * 100 : null;
}

function RankList({ title, rows, onPick }: { title: string; rows: StatePrice[]; onPick: (s: string) => void }) {
  return (
    <div>
      <div className="hud-label mb-1">{title}</div>
      <ol className="grid gap-0.5">
        {rows.map((s) => (
          <li key={s.state}>
            <button
              onClick={() => onPick(s.state)}
              className="flex w-full items-center justify-between rounded-sm px-2 py-1 text-left text-[14px] hover:bg-hud-cyan/10"
            >
              <span>{s.state}</span>
              <span className="tabular-nums text-hud-text">{kg(s.median_modal)}</span>
            </button>
          </li>
        ))}
      </ol>
    </div>
  );
}

// ---------------------------------------------------------------- dashboard

export default function Dashboard() {
  const [status, setStatus] = useState<Status | null>(null);
  const [commodities, setCommodities] = useState<CommoditySummary[]>([]);
  const [commodity, setCommodity] = useState<string | null>(null);
  const [days, setDays] = useState<DayCount[]>([]);
  const [sliderIndex, setSliderIndex] = useState(0); // moves while dragging
  const [dayIndex, setDayIndex] = useState<number | null>(null); // committed after a pause
  const [map, setMap] = useState<PriceMap | null>(null);
  const [national, setNational] = useState<Series | null>(null);
  const [focusSeries, setFocusSeries] = useState<Series | null>(null);
  const [selState, setSelState] = useState<string | null>(null);
  const [selMarket, setSelMarket] = useState<MarketPrice | null>(null);
  const [forecast, setForecast] = useState<ForecastResult | null>(null);
  const [modelScores, setModelScores] = useState<ModelScores | null>(null);
  const [groups, setGroups] = useState<MarketGroups | null>(null);
  const [colourMode, setColourMode] = useState<ColourMode>("price");
  const [highlightGroup, setHighlightGroup] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.status().then(setStatus).catch(() => setStatus(null));
    api.models().then(setModelScores).catch(() => setModelScores(null));
    api
      .commodities()
      .then((list) => {
        setCommodities(list);
        setCommodity((c) => c ?? list[0]?.commodity ?? null);
      })
      .catch((e: Error) => setError(e.message));
  }, []);

  useEffect(() => {
    if (!commodity) return;
    let live = true;
    Promise.all([api.dates(commodity), api.series(commodity, { days: 730 })])
      .then(([d, s]) => {
        if (!live) return;
        setDays(d);
        setSliderIndex(d.length - 1);
        setDayIndex(d.length - 1);
        setNational(s);
      })
      .catch((e: Error) => live && setError(e.message));
    return () => {
      live = false;
    };
  }, [commodity]);

  // Dragging the slider fires many events; only fetch once it settles.
  useEffect(() => {
    const id = setTimeout(() => setDayIndex(sliderIndex), 180);
    return () => clearTimeout(id);
  }, [sliderIndex]);

  const day = dayIndex !== null ? days[dayIndex]?.date : undefined;

  useEffect(() => {
    if (!commodity || !day) return;
    const ctl = new AbortController();
    api
      .map(commodity, day, ctl.signal)
      .then((m) => {
        setMap(m);
        setError(null);
      })
      .catch((e: Error) => e.name !== "AbortError" && setError(e.message));
    return () => ctl.abort();
  }, [commodity, day]);

  useEffect(() => {
    if (!commodity || (!selState && !selMarket)) return;
    const ctl = new AbortController();
    api
      .series(commodity, selMarket ? { market_id: selMarket.id, days: 730 } : { state: selState ?? undefined, days: 730 }, ctl.signal)
      .then(setFocusSeries)
      .catch(() => {});
    return () => ctl.abort();
  }, [commodity, selState, selMarket]);

  // Behaviour groups for the chosen commodity (they change rarely, so fetch once per commodity).
  useEffect(() => {
    if (!commodity) return;
    api
      .groups(commodity)
      .then((g) => {
        setGroups(g);
        setHighlightGroup(null);
      })
      .catch(() => setGroups(null));
  }, [commodity]);

  const groupOf = useMemo(() => new Map(groups?.members.map((m) => [m.market_id, m.group]) ?? []), [groups]);
  const selectedGroup = selMarket ? groups?.groups.find((g) => g.number === groupOf.get(selMarket.id)) : undefined;

  // Forecast for all India, or for the selected state.
  useEffect(() => {
    if (!commodity) return;
    const controller = new AbortController();
    api
      .forecast(commodity, selState ?? undefined, controller.signal)
      .then(setForecast)
      .catch(() => {});
    return () => controller.abort();
  }, [commodity, selState]);

  // The forecast belongs to whatever the side panel is showing.
  const forecastForPanel = forecast && forecast.state === (selState ?? "") ? forecast : null;
  const lastPoint = (series: Series | null) => series?.points[series.points.length - 1]?.median_modal ?? null;

  const stateRow = map?.states.find((s) => s.state === selState) ?? null;
  const sorted = useMemo(() => [...(map?.states ?? [])].sort((a, b) => a.median_modal - b.median_modal), [map]);
  const cheapest = sorted[0];
  const dearest = sorted[sorted.length - 1];
  const nationalChange = useMemo(() => changeOver(national?.points ?? []), [national]);
  const stateMarkets = useMemo(
    () => (map && selState ? map.markets.filter((m) => m.state === selState).sort((a, b) => a.modal - b.modal) : []),
    [map, selState],
  );
  const isLatest = dayIndex === days.length - 1;

  const pickState = (s: string | null) => {
    setSelState(s);
    setSelMarket(null);
  };

  return (
    <div className="hud-grid flex min-h-screen flex-col lg:h-full lg:min-h-0">
      {/* ------------------------------------------------------------ header */}
      <header className="flex flex-wrap items-center justify-between gap-3 border-b border-[var(--line)] px-5 py-3">
        <div>
          <h1 className="font-display text-2xl font-bold tracking-[0.3em] text-hud-cyan hud-glow">MANDIPULSE</h1>
          <p className="text-[13px] text-hud-dim">Wholesale prices reported by India&apos;s mandis (APMC markets), every day</p>
        </div>
        <div className="flex items-center gap-2 text-[13px] text-hud-dim">
          <span className="inline-block h-2.5 w-2.5 rounded-full bg-hud-green hud-blink" />
          Latest data: {status?.latest_price_date ? niceDate(status.latest_price_date) : "—"}
        </div>
      </header>

      {error && (
        <div className="border-b border-hud-red/50 bg-hud-red/10 px-5 py-1.5 text-[13px] text-hud-red">
          Can&apos;t reach the data API ({error}). Is the Django server running on port 8000?
        </div>
      )}

      {/* ---------------------------------------------------- commodity tabs */}
      <nav className="flex gap-2 overflow-x-auto px-5 pt-3" aria-label="Commodity">
        {commodities.map((c) => {
          const on = c.commodity === commodity;
          return (
            <button
              key={c.commodity}
              onClick={() => {
                setCommodity(c.commodity);
                pickState(null);
              }}
              aria-pressed={on}
              className={`border px-5 py-2 font-display text-lg font-semibold tracking-wider transition-colors ${
                on ? "border-hud-cyan bg-hud-cyan/15 text-hud-cyan" : "border-[var(--line)] text-hud-text hover:border-hud-cyan/60"
              }`}
            >
              {c.commodity}
            </button>
          );
        })}
      </nav>

      {/* ------------------------------------------------------------- KPIs */}
      <div className="grid grid-cols-2 gap-3 px-5 pt-3 lg:grid-cols-4">
        <Kpi
          label={`Typical price in India · ${day ? niceDate(day) : ""}`}
          value={map ? kg(map.national_median) : "—"}
          sub={map ? `${qtl(map.national_median)} · ${map.markets.length} mandis reporting` : undefined}
        />
        <Kpi
          label="Cheapest state"
          value={cheapest ? kg(cheapest.median_modal) : "—"}
          sub={cheapest?.state}
          tone="text-[#40e0ff]"
        />
        <Kpi label="Most expensive state" value={dearest ? kg(dearest.median_modal) : "—"} sub={dearest?.state} tone="text-hud-red" />
        <Kpi
          label="India, last 7 days"
          value={nationalChange === null ? "—" : `${nationalChange > 0 ? "▲" : "▼"} ${Math.abs(nationalChange).toFixed(0)}%`}
          sub={nationalChange === null ? "not enough history yet" : nationalChange > 0 ? "prices rising" : "prices falling"}
          tone={nationalChange === null ? "text-hud-dim" : nationalChange > 0 ? "text-hud-amber" : "text-hud-green"}
        />
      </div>

      {/* ------------------------------------------------------------- body */}
      <main className="grid min-h-0 flex-1 grid-cols-1 gap-3 p-5 pt-3 lg:grid-cols-[1fr_380px] lg:grid-rows-[minmax(0,1fr)]">
        {/* Map */}
        <div className="hud-panel relative flex min-h-[460px] flex-col lg:min-h-0">
          <div className="flex items-center justify-between px-4 pt-3 text-[13px]">
            <span className="text-hud-dim">
              {selState ? (
                <>Showing <span className="text-hud-cyan">{selState}</span> · click a dot for one mandi</>
              ) : (
                "Each state is coloured by its typical price. Click a state to zoom in."
              )}
            </span>
            <div className="flex items-center gap-2">
              <span className="text-hud-dim">Colour dots by</span>
              {(["price", "group"] as const).map((mode) => (
                <button
                  key={mode}
                  onClick={() => setColourMode(mode)}
                  aria-pressed={colourMode === mode}
                  className={`border px-2 py-0.5 ${colourMode === mode ? "border-hud-cyan bg-hud-cyan/15 text-hud-cyan" : "border-[var(--line)] hover:border-hud-cyan/60"}`}
                >
                  {mode === "price" ? "Price" : "Behaviour group"}
                </button>
              ))}
              {selState && (
                <button onClick={() => pickState(null)} className="ml-2 border border-[var(--line-strong)] px-3 py-0.5 text-hud-cyan hover:bg-hud-cyan/10">
                  ← All India
                </button>
              )}
            </div>
          </div>

          <div className="relative min-h-0 flex-1">
            <IndiaMap
              data={map}
              selectedState={selState}
              selectedMarket={selMarket?.id ?? null}
              onSelectState={pickState}
              onSelectMarket={(m) => {
                setSelMarket(m);
                setSelState(m.state);
              }}
              mode={colourMode}
              groupOf={groupOf}
              highlightGroup={highlightGroup}
            />
          </div>

          {/* Legend: one row, labels in Rs/kg so the colours have concrete meaning */}
          {colourMode === "group" && groups && (
            <div className="flex flex-wrap items-center gap-x-4 gap-y-1 border-t border-[var(--line)] px-4 py-2 text-[12px]">
              <span className="hud-label">Behaviour groups</span>
              {groups.groups.map((g) => (
                <button
                  key={g.number}
                  onClick={() => setHighlightGroup(highlightGroup === g.number ? null : g.number)}
                  className="flex items-center gap-1.5 hover:text-hud-cyan"
                >
                  <span style={{ color: GROUP_COLOURS[g.number % GROUP_COLOURS.length] }}>●</span>
                  <span className={highlightGroup === null || highlightGroup === g.number ? "" : "text-hud-dim"}>{g.label}</span>
                </button>
              ))}
            </div>
          )}
          {map && colourMode === "price" && (
            <div className="flex flex-wrap items-center gap-x-4 gap-y-1 border-t border-[var(--line)] px-4 py-2 text-[12px]">
              <span className="hud-label">vs India&apos;s {kg(map.national_median)}</span>
              {BINS.map((b) => (
                <span key={b.key} className="flex items-center gap-1.5">
                  <span className="inline-block h-3 w-4" style={{ background: b.fill, outline: `1px solid ${b.dot}` }} />
                  <span>{b.label}</span>
                  <span className="text-hud-dim">
                    {b.hi === Infinity
                      ? `>${kg(map.national_median * b.lo)}`
                      : b.lo === 0
                        ? `<${kg(map.national_median * b.hi)}`
                        : `${kg(map.national_median * b.lo).replace("/kg", "")}–${kg(map.national_median * b.hi).replace("₹", "").replace("/kg", "")}`}
                  </span>
                </span>
              ))}
            </div>
          )}

          {/* Timeline */}
          <div className="border-t border-[var(--line)] px-4 py-2">
            <div className="mb-1 flex items-center justify-between text-[13px]">
              <span className="hud-label">Date</span>
              <span className="text-hud-cyan">
                {days[sliderIndex] ? niceDate(days[sliderIndex].date) : "—"}
                {isLatest ? " · latest" : ""}
              </span>
              {!isLatest && days.length > 0 && (
                <button onClick={() => setSliderIndex(days.length - 1)} className="text-hud-dim underline hover:text-hud-cyan">
                  jump to latest
                </button>
              )}
            </div>
            <input
              type="range"
              className="hud-range"
              min={0}
              max={Math.max(0, days.length - 1)}
              value={sliderIndex}
              onChange={(e) => setSliderIndex(Number(e.target.value))}
              aria-label="Choose a date"
            />
            <div className="flex justify-between text-[12px] text-hud-dim">
              <span>{days[0] ? niceDate(days[0].date) : ""}</span>
              <span>{days.length ? niceDate(days[days.length - 1].date) : ""}</span>
            </div>
          </div>
        </div>

        {/* Side panel */}
        <div className="flex min-h-0 flex-col gap-3 overflow-y-auto">
          {selMarket ? (
            <Panel title="Mandi">
              <div className="font-display text-2xl font-bold text-hud-text">{selMarket.market}</div>
              <div className="mb-3 text-[13px] text-hud-dim">{selMarket.district}, {selMarket.state}</div>
              <div className="font-display text-3xl font-bold" style={{ color: map ? binFor(selMarket.modal, map.national_median).dot : undefined }}>
                {kg(selMarket.modal)}
              </div>
              <div className="text-[13px] text-hud-dim">
                {qtl(selMarket.modal)} · traded between {kg(selMarket.min)} and {kg(selMarket.max)}
              </div>
              {selMarket.anomaly && (
                <div className="mt-3 border border-hud-red/60 bg-hud-red/10 px-3 py-2 text-[13px] leading-5">
                  <span className="font-semibold text-hud-red">Unusual price.</span> {selMarket.reason}.
                </div>
              )}
              {selectedGroup && (
                <div className="mt-3 text-[13px] leading-5">
                  <span className="hud-label">Behaviour group</span>
                  <div>
                    <span style={{ color: GROUP_COLOURS[selectedGroup.number % GROUP_COLOURS.length] }}>●</span> {selectedGroup.label}
                  </div>
                  <div className="text-[12px] text-hud-dim">{selectedGroup.description}</div>
                </div>
              )}
              <div className="mt-4 hud-label">Price history at this mandi</div>
              <TrendChart points={focusSeries?.points ?? []} />
              <button onClick={() => setSelMarket(null)} className="mt-2 text-[13px] text-hud-dim underline hover:text-hud-cyan">
                back to {selMarket.state}
              </button>
            </Panel>
          ) : stateRow ? (
            <Panel title="State">
              <div className="font-display text-2xl font-bold text-hud-text">{stateRow.state}</div>
              <div className="mt-2 font-display text-3xl font-bold" style={{ color: map ? binFor(stateRow.median_modal, map.national_median).dot : undefined }}>
                {kg(stateRow.median_modal)}
              </div>
              <div className="text-[13px] text-hud-dim">
                typical price · {stateRow.markets} {stateRow.markets === 1 ? "mandi" : "mandis"} · {pctChange(stateRow.change_7d_pct)}
              </div>
              <div className="mt-4 hud-label">Price history in {stateRow.state}</div>
              <TrendChart points={focusSeries?.points ?? []} forecast={forecastForPanel?.points ?? []} />
              <ForecastSummary forecast={forecastForPanel} todayPrice={lastPoint(focusSeries)} />
              {stateMarkets.length > 0 && (
                <div className="mt-3 grid grid-cols-2 gap-3 text-[13px]">
                  <div>
                    <div className="hud-label">Cheapest mandi</div>
                    <button className="text-left hover:text-hud-cyan" onClick={() => setSelMarket(stateMarkets[0])}>
                      {stateMarkets[0].market}<br /><span className="text-hud-cyan">{kg(stateMarkets[0].modal)}</span>
                    </button>
                  </div>
                  <div>
                    <div className="hud-label">Most expensive mandi</div>
                    <button className="text-left hover:text-hud-cyan" onClick={() => setSelMarket(stateMarkets[stateMarkets.length - 1])}>
                      {stateMarkets[stateMarkets.length - 1].market}<br />
                      <span className="text-hud-red">{kg(stateMarkets[stateMarkets.length - 1].modal)}</span>
                    </button>
                  </div>
                </div>
              )}
            </Panel>
          ) : (
            <Panel title="India trend">
              <TrendChart points={national?.points ?? []} forecast={forecastForPanel?.points ?? []} />
              <ForecastSummary forecast={forecastForPanel} todayPrice={lastPoint(national)} />
              <p className="mt-2 text-[12px] leading-5 text-hud-dim">
                Typical (median) wholesale price across all reporting mandis. Retail prices are usually higher.
              </p>
            </Panel>
          )}

          <Panel title="Where it's cheapest and most expensive">
            <div className="grid gap-4">
              <RankList title="Cheapest" rows={sorted.slice(0, 5)} onPick={pickState} />
              <RankList title="Most expensive" rows={sorted.slice(-5).reverse()} onPick={pickState} />
            </div>
          </Panel>

          <Panel title="Unusual prices">
            <UnusualPrices
              markets={map?.markets ?? []}
              onPick={(m) => {
                setSelMarket(m);
                setSelState(m.state);
              }}
            />
          </Panel>

          <Panel title="Mandi behaviour groups">
            <GroupsPanel
              data={groups}
              highlight={highlightGroup}
              onHighlight={(g) => {
                setHighlightGroup(g);
                if (g !== null) setColourMode("group"); // show the group on the map too
              }}
              onPickMarket={(id) => {
                const m = map?.markets.find((x) => x.id === id);
                if (m) {
                  setSelMarket(m);
                  setSelState(m.state);
                }
              }}
            />
          </Panel>

          <Panel title="How accurate is the forecast?">
            <ModelAccuracy scores={modelScores} />
          </Panel>
        </div>
      </main>

      <footer className="border-t border-[var(--line)] px-5 py-1.5 text-[12px] text-hud-dim">
        Source: Agmarknet, Directorate of Marketing &amp; Inspection, via data.gov.in · Government Open Data License – India ·
        boundaries: DataMeet / Survey of India
      </footer>
    </div>
  );
}
