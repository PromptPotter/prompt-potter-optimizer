// Shared display formatters — one home for the number / time / currency
// formatting the dashboard repeats across panels. Import from here; never
// re-inline a copy.

// Percentage, one decimal — "42.0%". Non-finite → "—".
export function fmtPct1(v: number | null | undefined): string {
  return typeof v === "number" && Number.isFinite(v) ? `${(v * 100).toFixed(1)}%` : "—";
}

// Percentage, no decimals — "42%". Non-finite → "—".
export function fmtPct0(v: number | null | undefined): string {
  return typeof v === "number" && Number.isFinite(v) ? `${(v * 100).toFixed(0)}%` : "—";
}

// Signed percentage, no decimals — "+4%" / "-2%". Non-finite → "—".
export function fmtPctSigned(v: number | null | undefined): string {
  if (typeof v !== "number" || !Number.isFinite(v)) return "—";
  return `${v >= 0 ? "+" : ""}${(v * 100).toFixed(0)}%`;
}

// Short elapsed time — "840ms" / "4.20s" / "1.5m". Non-finite → "—".
export function fmtSecs(s: number | null | undefined): string {
  if (typeof s !== "number" || !Number.isFinite(s)) return "—";
  if (s < 1) return `${(s * 1000).toFixed(0)}ms`;
  if (s < 60) return `${s.toFixed(2)}s`;
  return `${(s / 60).toFixed(1)}m`;
}

// Coarse duration for cycle-scale spans — "45s" / "12m" / "2h 5m".
export function fmtDuration(sec: number): string {
  if (!Number.isFinite(sec) || sec < 0) return "—";
  if (sec < 90) return `${Math.round(sec)}s`;
  const m = Math.round(sec / 60);
  if (m < 90) return `${m}m`;
  const h = Math.floor(m / 60);
  const rm = m % 60;
  return rm === 0 ? `${h}h` : `${h}h ${rm}m`;
}

// A SILENCE on the time-ray — "2m" / "14h" / "3d". Distinct from fmtDuration because it
// answers a different question: fmtDuration measures how long something took, this measures
// how long nothing happened, so it renders nothing at all below the gap threshold.
//
// Under 90 s is deliberately empty, and the number is load-bearing rather than taste. The
// in-flight heartbeat fires every 10 s during every long await this package makes — the
// optimizer call, an L4 inner campaign, and `measure_sample`'s backend query, whose
// QUERY_TIMEOUT is 120 s. The ray keeps those heartbeats as gap SUPPRESSORS (it drops them
// from the rendered steps but counts them as proof of life), so a heartbeated 120 s query
// grows no gap at all. 90 s = nine missed heartbeats: it cannot be ordinary in-run latency.
//
// If anyone ever makes the server stop sending `llm_call_progress` on the ray, every backend
// query sprouts a spurious gap here. The coupling is commented at both ends.
export function fmtGap(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 90) return "";
  if (seconds < 90 * 60) return `${Math.round(seconds / 60)}m`;
  if (seconds < 36 * 3600) return `${Math.round(seconds / 3600)}h`;
  return `${Math.round(seconds / 86_400)}d`;
}

// USD spend — 4dp under a cent, else 2dp. "$0.0042" / "$1.30".
export function fmtUsd(n: number): string {
  return n < 0.01 ? `$${n.toFixed(4)}` : `$${n.toFixed(2)}`;
}

// Compact bare number — "3.4M" / "12.0k" / "840". For headline counts where
// the unit is named elsewhere (a "Tokens" chart title); use fmtTokens when the
// "tok" suffix belongs on the number itself.
export function fmtCompact(v: number): string {
  if (v >= 1_000_000) return `${(v / 1_000_000).toFixed(1)}M`;
  if (v >= 1_000) return `${(v / 1_000).toFixed(1)}k`;
  return v.toLocaleString();
}

// On-disk size — "0 B" / "12.4 KB" / "3.1 MB" / "1.20 GB". A missing / non-finite
// value renders "—", never "NaN B".
export function fmtBytes(n: number | null | undefined): string {
  if (n == null || !Number.isFinite(n)) return "—";
  if (n < 1024) return `${Math.max(0, Math.round(n))} B`;
  if (n < 1_048_576) return `${(n / 1024).toFixed(1)} KB`;
  if (n < 1_073_741_824) return `${(n / 1_048_576).toFixed(1)} MB`;
  return `${(n / 1_073_741_824).toFixed(2)} GB`;
}

// Token count — "920 tok" / "12k tok" / "3.4M tok".
export function fmtTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M tok`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(0)}k tok`;
  return `${n} tok`;
}

// Signed fixed-digit number — "+0.123" / "-0.045". Non-finite → "—".
export function fmtSigned(v: number | null | undefined, digits = 3): string {
  if (typeof v !== "number" || !Number.isFinite(v)) return "—";
  return `${v >= 0 ? "+" : ""}${v.toFixed(digits)}`;
}

// Fixed-digit number — null → "—", non-numbers pass through as String(v).
export function fmtNum(v: unknown, digits = 3): string {
  if (v == null) return "—";
  if (typeof v === "number") return v.toFixed(digits);
  return String(v);
}

// Composite-fitness scalar — 2dp. Non-finite → "—".
export function fmtFitness(v: number | null): string {
  if (v == null || !Number.isFinite(v)) return "—";
  return v.toFixed(2);
}

// Local wall-clock time of an ISO timestamp — bad input passes through.
export function fmtClock(s: unknown): string {
  if (!s) return "—";
  try {
    return new Date(String(s)).toLocaleTimeString();
  } catch {
    return String(s);
  }
}

// Local date + time of an ISO timestamp — bad input passes through.
export function fmtDateTime(s: unknown): string {
  if (!s) return "—";
  try {
    return new Date(String(s)).toLocaleString();
  } catch {
    return String(s);
  }
}

// Coarse age of an ISO timestamp, largest sensible unit — "3 weeks ago",
// "5 days ago", "2h ago", "just now". Empty on bad/absent input so a caller can
// drop it silently.
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

// Generic display fallback — null / empty → "—", else String(v).
export function fmtText(v: unknown): string {
  if (v == null || v === "") return "—";
  return String(v);
}

// Unknown-VALUE display — the one formatter for a config/param value of any
// shape: booleans as ON/OFF, objects/arrays as JSON (`pretty` indents),
// everything else via fmtText. Panels do not re-inline their own switch.
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

// Relative age in seconds → "30s ago" / "5m ago" / "2h ago" / "3d ago".
export function ageTextSeconds(seconds: number): string {
  const s = Math.max(0, Math.round(seconds));
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.round(s / 60)}m ago`;
  if (s < 86400) return `${Math.round(s / 3600)}h ago`;
  return `${Math.round(s / 86400)}d ago`;
}

// Relative age of an ISO timestamp — "30s ago" / "5m ago" / "2h ago" /
// "3d ago". Missing or unparseable input → "—".
export function ageText(iso: string | null | undefined): string {
  if (!iso) return "—";
  const t = Date.parse(iso);
  if (!Number.isFinite(t)) return "—";
  return ageTextSeconds((Date.now() - t) / 1000);
}
