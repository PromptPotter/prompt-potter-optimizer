import {
  type Estimate,
  type MeasuredLift,
  type MeasuredUnit,
  LIFT_SIDE_INFO,
  RECENT_STEP_S,
  SAMPLE_TAG_DIGITS,
  UNIT_FORMATS,
  type MeasurandUnit,
  type UnitFormat,
} from "@/lib/api/types";
import { cx } from "@/lib/cx";

type LiftSide = MeasuredLift["side"];

export const PLACEHOLDER = "—";

// A row's slot as every surface prints it (`domain/sample.py::sample_tag`): `#007`.
export function fmtSampleTag(position: number): string {
  return `#${String(position).padStart(SAMPLE_TAG_DIGITS, "0")}`;
}

// A `{name}` in served words stands for the served model's field of that name.
export function filled(words: string, model: object): string {
  const fields = model as Record<string, unknown>;
  return words.replace(/\{(\w+)\}/g, (_, name: string) => String(fields[name]));
}

export function unitPlural(unit: MeasuredUnit): string {
  return `${unit}s`;
}

export function unitCount(n: number, unit: MeasuredUnit): string {
  return `${n} ${n === 1 ? unit : unitPlural(unit)}`;
}

export function fmtPct1(v: number | null | undefined): string {
  return typeof v === "number" && Number.isFinite(v) ? `${(v * 100).toFixed(1)}%` : PLACEHOLDER;
}

export function cssPct(fraction: number): string {
  return `${fraction * 100}%`;
}

export function fmtSecs(s: number | null | undefined): string {
  if (typeof s !== "number" || !Number.isFinite(s)) return PLACEHOLDER;
  if (s < 1) return `${(s * 1000).toFixed(0)}ms`;
  if (s < 60) return `${s.toFixed(2)}s`;
  return `${(s / 60).toFixed(1)}m`;
}

export function fmtDuration(sec: number | null): string {
  if (sec == null || !Number.isFinite(sec) || sec < 0) return PLACEHOLDER;
  if (sec < 90) return `${Math.round(sec)}s`;
  const m = Math.round(sec / 60);
  if (m < 90) return `${m}m`;
  const h = Math.floor(m / 60);
  const rm = m % 60;
  return rm === 0 ? `${h}h` : `${h}h ${rm}m`;
}

// `RECENT_STEP_S` is counted in `llm_call_progress` heartbeats: the ray must carry them or every backend query shows a gap.
export function fmtGap(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < RECENT_STEP_S) return "";
  if (seconds < 90 * 60) return `${Math.round(seconds / 60)}m`;
  if (seconds < 36 * 3600) return `${Math.round(seconds / 3600)}h`;
  return `${Math.round(seconds / 86_400)}d`;
}

// `paired_reading.py::money_places`: three significant digits under a dollar, else two places.
function moneyPlaces(v: number): number {
  if (v === 0 || Math.abs(v) >= 1) return 2;
  const places = 2 - Math.floor(Math.log10(Math.abs(v)));
  return Math.abs(Number(fixed(v, places))) < 1 ? places : 2;
}

// A sum of money as `domain/spend.py::usd_text` prints it: the `usd` measurand's level.
export function fmtUsd(n: number): string {
  return fmtUnit("usd", n);
}

export function fmtCompact(v: number): string {
  if (v >= 1_000_000) return `${(v / 1_000_000).toFixed(1)}M`;
  if (v >= 1_000) return `${(v / 1_000).toFixed(1)}k`;
  return v.toLocaleString();
}

export function fmtBytes(n: number | null | undefined): string {
  if (n == null || !Number.isFinite(n)) return PLACEHOLDER;
  if (n < 1024) return `${Math.max(0, Math.round(n))} B`;
  if (n < 1_048_576) return `${(n / 1024).toFixed(1)} KB`;
  if (n < 1_073_741_824) return `${(n / 1_048_576).toFixed(1)} MB`;
  return `${(n / 1_073_741_824).toFixed(2)} GB`;
}

// Mirrors `views/render/primitives.py::fmt_pvalue`; `null` means nothing was tested.
export function fmtPValue(p: number | null): string {
  if (p == null) return PLACEHOLDER;
  if (p < 0.001) return "p<0.001 ***";
  if (p < 0.01) return `p=${p.toFixed(3)} **`;
  if (p < 0.05) return `p=${p.toFixed(2)} *`;
  return `p=${p.toFixed(2)} (ns)`;
}

const finite = (v: number | null | undefined): v is number =>
  typeof v === "number" && Number.isFinite(v);

const COMPACT_STEPS: [number, string][] = [
  [1_000_000, "M"],
  [1_000, "k"],
];

// Python's `f"{v:.{digits}f}"` on |v|: an exact binary tie rounds to even, where `toFixed` rounds
// it away from zero (1/40 as a rate reads `2%`, never `3%`). `toFixed(100)` is the exact expansion.
function fixed(v: number, digits: number): string {
  const exact = v.toFixed(100);
  const cut = exact.indexOf(".") + 1 + digits;
  const down = exact.slice(0, digits ? cut : cut - 1);
  const tie = /^50*$/.test(exact.slice(cut));
  return tie && Number(down.at(-1)) % 2 === 0 ? down : v.toFixed(digits);
}

// `paired_reading.py::_number`: a compact unit steps a thousand or more to one digit of k or M;
// −0 keeps its sign, as Python prints it.
function numberIn(f: UnitFormat, v: number, digits: number | null, signed: boolean): string {
  const scaled = f.scale * v;
  const size = Math.abs(scaled);
  const step = f.compact ? COMPACT_STEPS.find(([at]) => size >= at) : undefined;
  const places = digits ?? moneyPlaces(size);
  const text = step ? `${fixed(size / step[0], 1)}${step[1]}` : fixed(size, places);
  if (scaled < 0 || Object.is(scaled, -0)) return `-${text}`;
  return signed ? `+${text}` : text;
}

// A level in its unit, as `paired_reading.py::level_text` prints it.
export function fmtUnit(unit: MeasurandUnit, v: number | null | undefined): string {
  if (!finite(v)) return PLACEHOLDER;
  const f = UNIT_FORMATS[unit];
  return `${f.prefix}${numberIn(f, v, f.digits, false)}${f.suffix}`;
}


// Ties a value to its word and a bound to its pair, so a narrow line breaks only between them.
const NBSP = String.fromCharCode(0xa0);

function signedIn(unit: MeasurandUnit, v: number): string {
  const f = UNIT_FORMATS[unit];
  return numberIn(f, v, f.lift_digits, true);
}

// A lift in its measurand's unit, as `paired_reading.py::lift_text` prints it on every other surface.
export function fmtLift(unit: MeasurandUnit, v: number | null | undefined): string {
  if (!finite(v)) return PLACEHOLDER;
  return `${signedIn(unit, v)}${NBSP}${UNIT_FORMATS[unit].lift_word}`;
}

// An estimate is served as facts alone (`paired_reading.py::Estimate`); `fmtMetric`, `fmtBand`
// and `fmtEstimate` word it as its `point`, `band` and `text` do (the fixture
// `estimate-words.generated.json` holds them to Python's words).
// The value of an estimate, or of a metric's cell, in its unit: only a lift earns a sign.
export function fmtMetric(
  e: (Pick<Estimate, "unit" | "form"> & { value: number | null | undefined }) | null | undefined,
): string {
  if (e == null) return PLACEHOLDER;
  return e.form === "lift" ? fmtLift(e.unit, e.value) : fmtUnit(e.unit, e.value);
}

export function fmtBand(e: Estimate | null | undefined): string | null {
  if (e == null || e.ci_lo === null || e.ci_hi === null) return null;
  if (e.form === "level") return `[${fmtUnit(e.unit, e.ci_lo)}, ${fmtUnit(e.unit, e.ci_hi)}]`;
  return `[${signedIn(e.unit, e.ci_lo)},${NBSP}${signedIn(e.unit, e.ci_hi)}]`;
}

export function fmtEstimate(e: Estimate): string;
export function fmtEstimate(e: Estimate | null | undefined): string | null;
export function fmtEstimate(e: Estimate | null | undefined): string | null {
  if (e == null) return null;
  const band = fmtBand(e);
  return band === null ? fmtMetric(e) : `${fmtMetric(e)} ${band}`;
}



export function fmtSigned(v: number | null | undefined, digits: number): string {
  if (typeof v !== "number" || !Number.isFinite(v)) return PLACEHOLDER;
  return `${v >= 0 ? "+" : ""}${v.toFixed(digits)}`;
}

export function fmtLogits(v: number): string {
  return `${v.toFixed(2)} logits`;
}


// A side's served ink (`LIFT_SIDE_INFO`); no side reads as a band spanning zero.
export function sideTone(side: LiftSide | null | undefined): string {
  return cx("l4-eff", `tone-${LIFT_SIDE_INFO[side ?? "spans"].tone}`);
}

export function fmtClock(s: unknown): string {
  if (!s) return PLACEHOLDER;
  try {
    return new Date(String(s)).toLocaleTimeString();
  } catch {
    return String(s);
  }
}

export function fmtDateTime(s: unknown): string {
  if (!s) return PLACEHOLDER;
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
  if (v == null || v === "") return PLACEHOLDER;
  return String(v);
}

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
