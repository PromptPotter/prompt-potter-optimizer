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

// Generic display fallback — null / empty → "—", else String(v).
export function fmtText(v: unknown): string {
  if (v == null || v === "") return "—";
  return String(v);
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
