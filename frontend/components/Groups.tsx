"use client";

import { useState } from "react";
import { GROUP_COLOURS, type GroupMember, type MarketGroups } from "@/lib/api";

const WIDTH = 300; // scatter chart size in SVG units
const HEIGHT = 190;
const PAD = 12;

type Props = {
  data: MarketGroups | null;
  highlight: number | null; // group number to emphasise, or null for all
  onHighlight: (group: number | null) => void;
  onPickMarket: (marketId: number) => void;
};

/**
 * Behaviour groups found by K-means.
 * Top: every mandi as a dot on the PCA chart (similar mandis sit close together).
 * Bottom: each group with a plain description. Click a group to highlight it.
 */
export default function GroupsPanel({ data, highlight, onHighlight, onPickMarket }: Props) {
  const [hovered, setHovered] = useState<GroupMember | null>(null);

  if (!data || data.groups.length === 0) {
    return <p className="text-[13px] text-hud-dim">Groups not computed yet for this commodity.</p>;
  }

  // Scale PCA coordinates into the square chart.
  const xs = data.members.map((m) => m.x);
  const ys = data.members.map((m) => m.y);
  const minX = Math.min(...xs);
  const maxX = Math.max(...xs);
  const minY = Math.min(...ys);
  const maxY = Math.max(...ys);
  const toX = (v: number) => PAD + ((v - minX) / (maxX - minX || 1)) * (WIDTH - 2 * PAD);
  const toY = (v: number) => HEIGHT - PAD - ((v - minY) / (maxY - minY || 1)) * (HEIGHT - 2 * PAD);

  return (
    <div>
      <p className="mb-2 text-[13px] leading-5 text-hud-dim">
        K-means grouped {data.members.length} mandis by price level, weekly swings, recent trend and how closely
        they follow the all-India price. Each dot is a mandi; nearby dots behave alike.
      </p>

      <svg viewBox={`0 0 ${WIDTH} ${HEIGHT}`} className="w-full" onMouseLeave={() => setHovered(null)}>
        <g>
          {data.members.map((m) => {
            const faded = highlight !== null && m.group !== highlight;
            return (
              <circle
                key={m.market_id}
                cx={toX(m.x)}
                cy={toY(m.y)}
                r={faded ? 1.6 : 2.4}
                fill={GROUP_COLOURS[m.group % GROUP_COLOURS.length]}
                opacity={faded ? 0.15 : 0.85}
                className="cursor-pointer"
                onMouseEnter={() => setHovered(m)}
                onClick={() => onPickMarket(m.market_id)}
              />
            );
          })}
        </g>
      </svg>
      <div className="h-5 text-[12px] text-hud-dim">
        {hovered ? `${hovered.market}, ${hovered.state}` : "PCA view: the 4 behaviour numbers squeezed into 2D"}
      </div>

      <ul className="mt-2 grid gap-1">
        {data.groups.map((g) => {
          const on = highlight === g.number;
          return (
            <li key={g.number}>
              <button
                onClick={() => onHighlight(on ? null : g.number)}
                aria-pressed={on}
                className={`w-full border px-2 py-1.5 text-left ${on ? "border-hud-cyan bg-hud-cyan/10" : "border-transparent hover:border-[var(--line)]"}`}
              >
                <div className="flex items-center justify-between text-[14px]">
                  <span>
                    <span style={{ color: GROUP_COLOURS[g.number % GROUP_COLOURS.length] }}>●</span> {g.label}
                  </span>
                  <span className="text-[12px] text-hud-dim">{g.size} mandis</span>
                </div>
                <div className="text-[12px] leading-4 text-hud-dim">{g.description}</div>
              </button>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
