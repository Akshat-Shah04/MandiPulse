"use client";

import { geoGraticule10, geoMercator, geoPath, type GeoPermissibleObjects } from "d3-geo";
import { forwardRef, memo, useEffect, useImperativeHandle, useMemo, useRef, useState } from "react";
import { BASE_PATH, binFor, GROUP_COLOURS, kg, pctChange, qtl, type MarketPrice, type PriceMap, type StatePrice } from "@/lib/api";

export type ColourMode = "price" | "group";

type Feature = {
  type: "Feature";
  properties: { name: string; cx: number; cy: number };
  geometry: GeoPermissibleObjects;
};
type Collection = { type: "FeatureCollection"; features: Feature[] };

const W = 760;
const H = 820;

// ---------------------------------------------------------------- tooltip
// Lives outside the map's render tree: hovering only re-renders this box.

type Tip = { x: number; y: number; title: string; lines: string[] } | null;
type TipHandle = { show: (t: Tip) => void };

const Tooltip = forwardRef<TipHandle>(function Tooltip(_, ref) {
  const [tip, setTip] = useState<Tip>(null);
  useImperativeHandle(ref, () => ({ show: setTip }), []);
  if (!tip) return null;
  return (
    <div
      className="hud-panel pointer-events-none absolute z-20 w-64 px-3 py-2 text-[13px] leading-6"
      style={{ left: tip.x + 18, top: tip.y + 18, transform: tip.x > 460 ? "translateX(calc(-100% - 36px))" : undefined }}
    >
      <div className="font-display text-base font-semibold text-hud-cyan">{tip.title}</div>
      {tip.lines.map((l) => (
        <div key={l} className="text-hud-text">{l}</div>
      ))}
    </div>
  );
});

// ---------------------------------------------------------------- layers

const StatesLayer = memo(function StatesLayer({
  features, path, byState, national, selected, mode,
}: {
  features: Feature[];
  path: ReturnType<typeof geoPath>;
  byState: Map<string, StatePrice>;
  national: number | null;
  selected: string | null;
  mode: ColourMode;
}) {
  return (
    <g>
      {features.map((f) => {
        const name = f.properties.name;
        const sp = byState.get(name);
        let fill = "#071318"; // no data
        if (mode === "group") fill = sp ? "#0b2029" : "#071318"; // quiet background so group dots stand out
        else if (sp && national) fill = binFor(sp.median_modal, national).fill;
        const isSel = name === selected;
        return (
          <path
            key={name}
            data-state={name}
            d={path(f.geometry) ?? undefined}
            fill={fill}
            stroke={isSel ? "#40e0ff" : "rgba(64,224,255,0.45)"}
            strokeWidth={isSel ? 2.4 : 0.8}
            vectorEffect="non-scaling-stroke"
            className="cursor-pointer"
          />
        );
      })}
    </g>
  );
});

const MarketsLayer = memo(function MarketsLayer({
  points, national, selected, zoomed, mode, groupOf, highlightGroup,
}: {
  points: { m: MarketPrice; x: number; y: number }[];
  national: number | null;
  selected: number | null;
  zoomed: boolean;
  mode: ColourMode;
  groupOf: Map<number, number>;
  highlightGroup: number | null;
}) {
  const r = zoomed ? 0.9 : 2;
  return (
    <g>
      {points.map(({ m, x, y }) => {
        // Unusual prices get a bigger red dot with a ring so they stand out.
        let radius = m.anomaly ? r * 1.6 : r;
        if (m.id === selected) radius = r * 2.2;
        let colour = national ? binFor(m.modal, national).dot : "#40e0ff";
        let opacity = 1;
        if (mode === "group") {
          const group = groupOf.get(m.id);
          colour = group === undefined ? "#3a5560" : GROUP_COLOURS[group % GROUP_COLOURS.length];
          if (highlightGroup !== null && group !== highlightGroup) opacity = 0.12;
        }
        if (m.anomaly && mode === "price") colour = "#ff3b4f";
        return (
          <g key={m.id}>
            {m.anomaly && mode === "price" && (
              <circle cx={x} cy={y} r={radius * 2.4} fill="none" stroke="#ff3b4f" strokeWidth={1} vectorEffect="non-scaling-stroke" pointerEvents="none" />
            )}
            <circle
              data-market={m.id}
              cx={x}
              cy={y}
              r={radius}
              fill={colour}
              opacity={opacity}
              stroke={m.id === selected ? "#fff" : "#02070b"}
              strokeWidth={0.6}
              vectorEffect="non-scaling-stroke"
              className="cursor-pointer"
            />
          </g>
        );
      })}
    </g>
  );
});

// ---------------------------------------------------------------- map

type Props = {
  data: PriceMap | null;
  selectedState: string | null;
  selectedMarket: number | null;
  onSelectState: (state: string | null) => void;
  onSelectMarket: (m: MarketPrice) => void;
  mode: ColourMode;
  groupOf: Map<number, number>; // market id -> behaviour group number
  highlightGroup: number | null;
};

export default function IndiaMap({
  data, selectedState, selectedMarket, onSelectState, onSelectMarket, mode, groupOf, highlightGroup,
}: Props) {
  const [geo, setGeo] = useState<Collection | null>(null);
  const tipRef = useRef<TipHandle>(null);
  const boxRef = useRef<HTMLDivElement>(null);
  const lastTipKey = useRef<string>("");

  useEffect(() => {
    fetch(`${BASE_PATH}/geo/india_states.geojson`)
      .then((r) => r.json())
      .then(setGeo)
      .catch(() => setGeo(null));
  }, []);

  const { path, projection } = useMemo(() => {
    const projection = geoMercator();
    if (geo) projection.fitExtent([[16, 16], [W - 16, H - 16]], geo as unknown as GeoPermissibleObjects);
    return { projection, path: geoPath(projection) };
  }, [geo]);

  const graticule = useMemo(() => (geo ? path(geoGraticule10()) : null), [geo, path]);
  const outline = useMemo(() => (geo ? path(geo as unknown as GeoPermissibleObjects) : null), [geo, path]);
  const byState = useMemo(() => new Map(data?.states.map((s) => [s.state, s]) ?? []), [data]);
  const byMarket = useMemo(() => new Map(data?.markets.map((m) => [m.id, m]) ?? []), [data]);
  const national = data?.national_median ?? null;

  const points = useMemo(
    () =>
      (data?.markets ?? []).flatMap((m) => {
        if (m.lat === null || m.lon === null) return [];
        const p = projection([m.lon, m.lat]);
        return p ? [{ m, x: p[0], y: p[1] }] : [];
      }),
    [data, projection],
  );

  const transform = useMemo(() => {
    if (!geo || !selectedState) return "translate(0px,0px) scale(1)";
    const f = geo.features.find((x) => x.properties.name === selectedState);
    if (!f) return "translate(0px,0px) scale(1)";
    const [[x0, y0], [x1, y1]] = path.bounds(f.geometry);
    const k = Math.min(5, 0.82 / Math.max((x1 - x0) / W, (y1 - y0) / H));
    return `translate(${W / 2 - (k * (x0 + x1)) / 2}px,${H / 2 - (k * (y0 + y1)) / 2}px) scale(${k})`;
  }, [geo, selectedState, path]);

  // One delegated handler for the whole map instead of one per element.
  const describe = (target: Element): { key: string; title: string; lines: string[] } | null => {
    const mid = target.getAttribute("data-market");
    if (mid) {
      const m = byMarket.get(Number(mid));
      if (!m) return null;
      return {
        key: `m${mid}`,
        title: m.market,
        lines: [
          `${m.district}, ${m.state}`,
          `${kg(m.modal)}  ·  ${qtl(m.modal)}`,
          `Range ${kg(m.min)} – ${kg(m.max)}`,
          ...(m.anomaly ? [`⚠ Unusual: ${m.reason}`] : []),
        ],
      };
    }
    const st = target.getAttribute("data-state");
    if (st) {
      const s = byState.get(st);
      return {
        key: `s${st}`,
        title: st,
        lines: s
          ? [`${kg(s.median_modal)} typical  ·  ${s.markets} ${s.markets === 1 ? "mandi" : "mandis"}`, pctChange(s.change_7d_pct), "Click to zoom in"]
          : ["No mandi reported this commodity today"],
      };
    }
    return null;
  };

  return (
    <div ref={boxRef} className="relative h-full w-full">
      <svg
        viewBox={`0 0 ${W} ${H}`}
        className="h-full w-full"
        role="img"
        aria-label="Map of India coloured by price relative to the national median"
        onMouseMove={(e) => {
          const d = describe(e.target as Element);
          const box = boxRef.current?.getBoundingClientRect();
          if (!d || !box) {
            if (lastTipKey.current) tipRef.current?.show(null);
            lastTipKey.current = "";
            return;
          }
          lastTipKey.current = d.key;
          tipRef.current?.show({ x: e.clientX - box.left, y: e.clientY - box.top, title: d.title, lines: d.lines });
        }}
        onMouseLeave={() => {
          lastTipKey.current = "";
          tipRef.current?.show(null);
        }}
        onClick={(e) => {
          const t = e.target as Element;
          const mid = t.getAttribute("data-market");
          if (mid) {
            const m = byMarket.get(Number(mid));
            if (m) onSelectMarket(m);
            return;
          }
          const st = t.getAttribute("data-state");
          onSelectState(st && st !== selectedState ? st : null);
        }}
      >
        <g style={{ transform, transition: "transform 650ms cubic-bezier(.2,.8,.2,1)", willChange: "transform" }}>
          {graticule && <path d={graticule} fill="none" stroke="rgba(64,224,255,0.07)" vectorEffect="non-scaling-stroke" />}
          {geo && (
            <StatesLayer features={geo.features} path={path} byState={byState} national={national} selected={selectedState} mode={mode} />
          )}
          {/* Soft halo on the coastline: a wide faint stroke, no blur filter */}
          {outline && (
            <path d={outline} fill="none" stroke="rgba(64,224,255,0.25)" strokeWidth={4} vectorEffect="non-scaling-stroke" pointerEvents="none" />
          )}
          <MarketsLayer
            points={points}
            national={national}
            selected={selectedMarket}
            zoomed={selectedState !== null}
            mode={mode}
            groupOf={groupOf}
            highlightGroup={highlightGroup}
          />
        </g>
      </svg>
      <Tooltip ref={tipRef} />
      {!geo && <div className="absolute inset-0 grid place-items-center hud-label">Loading map…</div>}
    </div>
  );
}
