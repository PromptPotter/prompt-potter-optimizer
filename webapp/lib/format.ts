// Shared display formatters — import from here, never re-inline a copy.

import type { MeasuredUnit, MetricSpec } from "@/lib/api/types";

// The browser's half of the engine's one noun for a measured row
// (`dashboard.json::measured_unit`): never pick it off a local flag, never pluralise inline.
export function unitPlural(unit: MeasuredUnit): string {
  return `${unit}s`;
}

export function unitCount(n: number, unit: MeasuredUnit): string {
  return `${n} ${n === 1 ? unit : unitPlural(unit)}`;
}

export function fmtPct1(v: number | null | undefined): string {
  return typeof v === "number" && Number.isFinite(v) ? `${(v * 100).toFixed(1)}%` : "—";
}

export function fmtPct0(v: number | null | undefined): string {
  return typeof v === "number" && Number.isFinite(v) ? `${(v * 100).toFixed(0)}%` : "—";
}

export function fmtSecs(s: number | null | undefined): string {
  if (typeof s !== "number" || !Number.isFinite(s)) return "—";
  if (s < 1) return `${(s * 1000).toFixed(0)}ms`;
  if (s < 60) return `${s.toFixed(2)}s`;
  return `${(s / 60).toFixed(1)}m`;
}

export function fmtDuration(sec: number): string {
  if (!Number.isFinite(sec) || sec < 0) return "—";
  if (sec < 90) return `${Math.round(sec)}s`;
  const m = Math.round(sec / 60);
  if (m < 90) return `${m}m`;
  const h = Math.floor(m / 60);
  const rm = m % 60;
  return rm === 0 ? `${h}h` : `${h}h ${rm}m`;
}

// A silence on the time-ray; empty under 90 s = nine missed `llm_call_progress` heartbeats.
// Stop serving those heartbeats on the ray and every backend query sprouts a spurious gap.
export function fmtGap(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 90) return "";
  if (seconds < 90 * 60) return `${Math.round(seconds / 60)}m`;
  if (seconds < 36 * 3600) return `${Math.round(seconds / 3600)}h`;
  return `${Math.round(seconds / 86_400)}d`;
}

export function fmtUsd(n: number): string {
  return n < 1 ? `$${n.toFixed(4)}` : `$${n.toFixed(2)}`;
}

export function fmtUsdCents(n: number): string {
  return n > 0 && n < 0.005 ? "<$0.01" : `$${n.toFixed(2)}`;
}

// Keeps the `:suffix`: it routes and bills differently, so it names a different run.
export function shortModel(id: string): string {
  return id.slice(id.lastIndexOf("/") + 1);
}

// Browser twin of `routers/auth.py::_provider_from_model` — change both. A colon left of the
// slash is a gateway prefix ("groq:openai/…"), right of it the model's routing suffix.
export function vendorOf(id: string): string {
  const slash = id.indexOf("/");
  const colon = id.indexOf(":");
  if (slash === -1) return (colon === -1 ? id : id.slice(0, colon)).toLowerCase();
  return id.slice(colon !== -1 && colon < slash ? colon + 1 : 0, slash).toLowerCase();
}

export function fmtCompact(v: number): string {
  if (v >= 1_000_000) return `${(v / 1_000_000).toFixed(1)}M`;
  if (v >= 1_000) return `${(v / 1_000).toFixed(1)}k`;
  return v.toLocaleString();
}

export function fmtBytes(n: number | null | undefined): string {
  if (n == null || !Number.isFinite(n)) return "—";
  if (n < 1024) return `${Math.max(0, Math.round(n))} B`;
  if (n < 1_048_576) return `${(n / 1024).toFixed(1)} KB`;
  if (n < 1_073_741_824) return `${(n / 1_048_576).toFixed(1)} MB`;
  return `${(n / 1_073_741_824).toFixed(2)} GB`;
}

export function fmtTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M tok`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(0)}k tok`;
  return `${n} tok`;
}

// Mirrors `terminal/primitives.py::fmt_pvalue`. `null` means nothing was tested — never render
// it as a test that found nothing.
export function fmtPValue(p: number | null): string {
  if (p == null) return "—";
  if (p < 0.001) return "p<0.001 ***";
  if (p < 0.01) return `p=${p.toFixed(3)} **`;
  if (p < 0.05) return `p=${p.toFixed(2)} *`;
  return `p=${p.toFixed(2)} (ns)`;
}

export type MetricUnit = MetricSpec["unit"];

const UNIT_FORMAT: Record<MetricUnit, (v: number) => string> = {
  level: (v) => v.toFixed(3),
  delta: (v) => fmtSigned(v),
  // Not fmtDuration: a sub-second reply would round to "1s" where the terminal prints 0.8.
  seconds: fmtSecs,
  usd: fmtUsd,
  tokens: fmtTokens,
  rank: (v) => v.toFixed(1),
  rounds: (v) => v.toFixed(1),
  composed: (v) => v.toFixed(3),
};

export function fmtMetricValue(unit: MetricUnit, v: number | null): string {
  return v == null ? "—" : UNIT_FORMAT[unit](v);
}

export function fmtMetricInterval(unit: MetricUnit, lo: number | null, hi: number | null): string {
  if (lo == null || hi == null) return "—";
  return `[${UNIT_FORMAT[unit](lo)}, ${UNIT_FORMAT[unit](hi)}]`;
}

// Campaign ids are `{dataset}__{rand6}`; the suffix is what tells two runs of one origin apart.
export function shortId(campaignId: string): string {
  const cut = campaignId.lastIndexOf("__");
  return cut === -1 ? campaignId : campaignId.slice(cut + 2);
}

export function fmtSigned(v: number | null | undefined, digits = 3): string {
  if (typeof v !== "number" || !Number.isFinite(v)) return "—";
  return `${v >= 0 ? "+" : ""}${v.toFixed(digits)}`;
}

// θ is in logits, so it never renders as a percent.
export function fmtTheta(v: number | null | undefined): string {
  if (typeof v !== "number" || !Number.isFinite(v)) return "—";
  return `θ ${fmtSigned(v, 2)}`;
}

// The one rule every effect table colours on. A missing bound is flat: nothing was tested.
export function effectTone(lo: number | null, hi: number | null): string {
  if (lo == null || hi == null) return "l4-eff-flat";
  return lo > 0 ? "l4-eff-pos" : hi < 0 ? "l4-eff-neg" : "l4-eff-flat";
}

export function fmtNum(v: unknown, digits = 3): string {
  if (v == null) return "—";
  if (typeof v === "number") return v.toFixed(digits);
  return String(v);
}

export function fmtFitness(v: number | null): string {
  if (v == null || !Number.isFinite(v)) return "—";
  return v.toFixed(2);
}

export function fmtClock(s: unknown): string {
  if (!s) return "—";
  try {
    return new Date(String(s)).toLocaleTimeString();
  } catch {
    return String(s);
  }
}

export function fmtDateTime(s: unknown): string {
  if (!s) return "—";
  try {
    return new Date(String(s)).toLocaleString();
  } catch {
    return String(s);
  }
}

export function fmtAgo(s: unknown): string {
  if (!s) return "";
  const t = new Date(String(s)).getTime();
  if (!Number.isFinite(t)) return "";
  const mins = Math.floor((Date.now() - t) / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  if (days < 7) return `${days} day${days === 1 ? "" : "s"} ago`;
  const weeks = Math.floor(days / 7);
  return `${weeks} week${weeks === 1 ? "" : "s"} ago`;
}

export function fmtText(v: unknown): string {
  if (v == null || v === "") return "—";
  return String(v);
}

// The one formatter for a config/param value of any shape; panels never re-inline the switch.
export function fmtValue(v: unknown, opts?: { pretty?: boolean }): string {
  if (typeof v === "boolean") return v ? "ON" : "OFF";
  if (v != null && typeof v === "object") {
    try {
      return opts?.pretty ? JSON.stringify(v, null, 2) : JSON.stringify(v);
    } catch {
      return String(v);
    }
  }
  return fmtText(v);
}

export function ageTextSeconds(seconds: number): string {
  const s = Math.max(0, Math.round(seconds));
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.round(s / 60)}m ago`;
  if (s < 86400) return `${Math.round(s / 3600)}h ago`;
  return `${Math.round(s / 86400)}d ago`;
}

export function ageText(iso: string | null | undefined): string {
  if (!iso) return "—";
  const t = Date.parse(iso);
  if (!Number.isFinite(t)) return "—";
  return ageTextSeconds((Date.now() - t) / 1000);
}
