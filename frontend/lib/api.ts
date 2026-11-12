export type CommoditySummary = {
  commodity: string;
  days: number;
  markets: number;
  first_date: string;
  last_date: string;
};

export type StatePrice = {
  state: string;
  median_modal: number;
  min: number;
  max: number;
  markets: number;
  change_7d_pct: number | null;
};

export type MarketPrice = {
  id: number;
  market: string;
  district: string;
  state: string;
  lat: number | null;
  lon: number | null;
  min: number;
  max: number;
  modal: number;
  anomaly: boolean;
  reason: string; // plain-English explanation when anomaly is true
};

export type PriceMap = {
  commodity: string;
  date: string;
  unit: string;
  national_median: number;
  national_p25: number;
  national_p75: number;
  states: StatePrice[];
  markets: MarketPrice[];
};

export type SeriesPoint = { date: string; median_modal: number; markets: number };
export type Series = { commodity: string; unit: string; points: SeriesPoint[] };
export type DayCount = { date: string; markets: number };
export type ForecastPoint = { date: string; yhat: number; lo: number; hi: number; model: string };
export type ForecastResult = {
  commodity: string;
  state: string;
  available: boolean;
  origin?: string;
  points: ForecastPoint[];
};
export type ModelScore = {
  model: string;
  mape: number;
  mae: number;
  used_for_days: number[];
  mape_by_day: number[];
};
export type ModelScores = { trained_at: string | null; models: ModelScore[] };

export type MarketGroupInfo = {
  number: number;
  label: string; // e.g. "Cheap · volatile · own path"
  description: string;
  size: number;
  price_vs_india_pct: number;
  weekly_swing_pct: number;
  trend_pct_per_week: number;
  follows_india: number;
};
export type GroupMember = { market_id: number; market: string; state: string; group: number; x: number; y: number };
export type MarketGroups = { commodity: string; groups: MarketGroupInfo[]; members: GroupMember[] };

export type Status = {
  facts: number;
  latest_price_date: string | null;
  last_successful_ingest: string | null;
  source: string;
};

async function get<T>(path: string, params: Record<string, string | number | undefined> = {}, signal?: AbortSignal): Promise<T> {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== "") q.set(k, String(v));
  const res = await fetch(`/api/${path}${q.size ? `?${q}` : ""}`, { signal });
  if (!res.ok) throw new Error(`${path}: HTTP ${res.status}`);
  return res.json() as Promise<T>;
}

export const api = {
  commodities: () => get<CommoditySummary[]>("commodities"),
  dates: (commodity: string) => get<DayCount[]>("dates", { commodity }),
  map: (commodity: string, date?: string, signal?: AbortSignal) => get<PriceMap>("map", { commodity, date }, signal),
  series: (commodity: string, opts: { state?: string; market_id?: number; days?: number } = {}, signal?: AbortSignal) =>
    get<Series>("series", { commodity, ...opts }, signal),
  status: () => get<Status>("status"),
  forecast: (commodity: string, state?: string, signal?: AbortSignal) =>
    get<ForecastResult>("forecast", { commodity, state }, signal),
  models: () => get<ModelScores>("models"),
  groups: (commodity: string) => get<MarketGroups>("groups", { commodity }),
};

/** Colours for behaviour groups. Red is kept free for unusual prices. */
export const GROUP_COLOURS = ["#40e0ff", "#ffb340", "#b388ff", "#3dffa8", "#ff7ac6", "#f5f5a0"];

/** Friendly names for the model ids used by the backend. */
export const modelLabel: Record<string, string> = {
  naive: "Same as today",
  seasonal_naive: "Same weekday last week",
  ridge: "Linear regression",
  random_forest: "Random forest",
  lstm: "LSTM neural network",
};

// ------------------------------------------------------------------ formatting
// The API speaks Rs per quintal (100 kg), as Agmarknet does. People think in
// Rs per kg, so the UI leads with that and shows quintal as the trade figure.

const inr0 = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 0 });

/** Rs/quintal -> "₹27/kg" (one decimal below ₹10) */
export const kg = (perQuintal: number) => {
  const v = perQuintal / 100;
  return `₹${v < 10 ? v.toFixed(1) : inr0.format(v)}/kg`;
};

/** Rs/quintal -> "₹2,750/qtl" */
export const qtl = (perQuintal: number) => `₹${inr0.format(perQuintal)}/qtl`;

export const pctChange = (x: number | null) =>
  x === null ? "no data a week ago" : `${x > 0 ? "▲" : x < 0 ? "▼" : "■"} ${Math.abs(x).toFixed(0)}% vs last week`;

export const niceDate = (iso: string) =>
  new Date(`${iso}T00:00:00`).toLocaleDateString("en-IN", { day: "numeric", month: "short", year: "numeric" });

export const stateCode: Record<string, string> = {
  "Andaman and Nicobar Islands": "AN", "Andhra Pradesh": "AP", "Arunachal Pradesh": "AR", Assam: "AS",
  Bihar: "BR", Chandigarh: "CH", Chhattisgarh: "CG", "Dadra and Nagar Haveli and Daman and Diu": "DD",
  Delhi: "DL", Goa: "GA", Gujarat: "GJ", Haryana: "HR", "Himachal Pradesh": "HP", "Jammu and Kashmir": "JK",
  Jharkhand: "JH", Karnataka: "KA", Kerala: "KL", Ladakh: "LA", Lakshadweep: "LD", "Madhya Pradesh": "MP",
  Maharashtra: "MH", Manipur: "MN", Meghalaya: "ML", Mizoram: "MZ", Nagaland: "NL", Odisha: "OD",
  Puducherry: "PY", Punjab: "PB", Rajasthan: "RJ", Sikkim: "SK", "Tamil Nadu": "TN", Telangana: "TS",
  Tripura: "TR", "Uttar Pradesh": "UP", Uttarakhand: "UK", "West Bengal": "WB",
};

// ------------------------------------------------------------------ colour bins
// Five plain-language classes relative to the national median. Discrete bins
// read faster than a continuous ramp: one glance says "cheap" or "dear".

export type Bin = { key: string; label: string; lo: number; hi: number; fill: string; dot: string };

export const BINS: Bin[] = [
  { key: "much-cheaper", label: "Much cheaper", lo: 0, hi: 0.75, fill: "#0b6f8c", dot: "#40e0ff" },
  { key: "cheaper", label: "Cheaper", lo: 0.75, hi: 0.93, fill: "#0e4a5e", dot: "#7fe9ff" },
  { key: "around", label: "Around national", lo: 0.93, hi: 1.07, fill: "#1c3540", dot: "#cdeff7" },
  { key: "dearer", label: "Dearer", lo: 1.07, hi: 1.33, fill: "#6b4513", dot: "#ffb340" },
  { key: "much-dearer", label: "Much dearer", lo: 1.33, hi: Infinity, fill: "#7a1d27", dot: "#ff5a6a" },
];

export function binFor(value: number, nationalMedian: number): Bin {
  const r = value / nationalMedian;
  return BINS.find((b) => r >= b.lo && r < b.hi) ?? BINS[2];
}
