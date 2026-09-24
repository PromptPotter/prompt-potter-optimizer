import type { DashboardSnapshot } from "@/lib/poll";
import { useDashboard } from "@/lib/hooks/useDashboard";
import { headlineStats } from "@/lib/derivations";
import { fmtNum, fmtClock } from "@/lib/format";
import { cx } from "@/lib/cx";
import { CardFrame } from "@/components/ui";
import { FreqChart } from "@/components/eval/FreqChart";
import { TrendChart } from "@/components/eval/TrendChart";
import { CostStrip } from "@/components/eval/CostStrip";

// Fields surfaced elsewhere, or withheld from the UI.
const SHOWN_ELSEWHERE = new Set([
  "cycle_id", "wallclock_serialized_at",
  "best", "total_queries_scored", "last_query_elapsed_s",
  "composite_fitness_formula",
  "current_round",
  "current_query_payload",
  "state", "round", "candidate",
  "patience",
  "rounds",
]);

const KNOWN_ORDER = [
  "origin_acc", "current_acc", "n_variants", "sp_budget_round",
  "total_backend_calls", "error_count", "degraded_count", "backend_retry_count",
  "state_since", "stop_reason",
];

const WARN_IF_POSITIVE = new Set(["error_count", "degraded_count", "backend_retry_count"]);

const FORMATTERS: Record<string, (v: unknown) => string> = {
  origin_acc: (v) => fmtNum(v),
  current_acc: (v) => fmtNum(v),
  state_since: fmtClock,
};

export function LiveStateCard() {
  const { dash } = useDashboard();
  const formula = dash?.composite_fitness_formula || "—";

  const items: [string, unknown][] = [];
  const seen = new Set(SHOWN_ELSEWHERE);
  if (dash) {
    const { origin } = headlineStats(dash);
    if (origin != null) {
      items.push(["origin_acc", origin]);
      seen.add("origin_acc");
    }
    // Located off the round's incumbency stamp, never by position.
    const round0 = dash.rounds.find((r) => r.round === 0);
    const originSamples = round0?.candidates.find((c) => c.is_winner)?.scored_samples;
    if (typeof originSamples === "number") {
      items.push(["origin_samples", originSamples]);
      seen.add("origin_samples");
    }
    for (const k of KNOWN_ORDER) {
      if (!(k in dash) || seen.has(k)) continue;
      seen.add(k);
      items.push([k, (dash as unknown as Record<string, unknown>)[k]]);
    }
    for (const [k, v] of Object.entries(dash)) {
      if (seen.has(k)) continue;
      if (typeof v === "object" && v !== null) continue;
      items.push([k, v]);
    }
  }

  const payload = dash?.current_query_payload ?? "";
  const payloadEmpty = payload === "";
  const payloadText = payloadEmpty
    ? dash?.state === "scoring"
      ? "in flight, payload not exposed"
      : "no query in flight"
    : payload;

  return (
    <CardFrame
      className="live-state-card"
      headingTag="h2"
      title="Live state"
      actions={<span className="lsc-source">all dashboard.json fields</span>}
    >
      <div className="formula-row" title="composite_fitness_formula">{formula}</div>
      <div className="kv-grid">
        {items.map(([k, v]) => {
          const fmt = FORMATTERS[k] ?? ((x: unknown) => (x == null ? "—" : String(x)));
          const display = fmt(v);
          let cls = "";
          if (v == null || v === "" || display === "—") cls = "muted";
          else if (WARN_IF_POSITIVE.has(k) && Number(v) > 0) cls = "warn";
          else if (k === "state" && v === "scoring") cls = "ok";
          else if (k === "state" && v === "stopped") cls = "warn";
          const label = k.replace(/_/g, " ");
          return (
            <div key={k} className="kv">
              <div className="kv-label">{label}</div>
              <div className={`kv-val ${cls}`} title={String(v ?? "")}>{display}</div>
            </div>
          );
        })}
        {items.length === 0 && (
          <div className="kv">
            <div className="kv-val muted">Waiting for first dashboard.json poll…</div>
          </div>
        )}
      </div>
      <div className="var-label">In-flight query payload</div>
      <div className={cx("payload-block", payloadEmpty && "empty")}>{payloadText}</div>
      <BackendWarnings dash={dash} />
      <PoBBBackfillLog dash={dash} />
      <div className="lsc-charts">
        <TrendChart />
        <CostStrip />
        <FreqChart />
      </div>
    </CardFrame>
  );
}

function PoBBBackfillLog({ dash }: { dash: DashboardSnapshot | null }) {
  // Paired-PoBB telemetry: one entry per sample where a prior gained a fresh measurement
  // (docs/methods/candidate-elimination.md).
  const log = dash?.backfill_log;
  if (!log || log.length === 0) return null;
  return (
    <>
      <div className="var-label">Paired-PoBB backfill (last {log.length})</div>
      <div className="payload-block">
        {log.slice().reverse().map((e, i) => {
          const { round, candidate_idx: cidx, candidate_total: ctot, sample_id: sid, prior_ids: priors } = e;
          return (
            <div key={i} className="log-row">
              <span className="log-dim">
                R{round} C{cidx + 1}/{ctot}
              </span>{" "}
              <span className="log-mark">↻</span> #{sid}
              {priors.length > 0 && <span className="log-dim"> — {priors.join(", ")}</span>}
            </div>
          );
        })}
      </div>
    </>
  );
}

function BackendWarnings({ dash }: { dash: DashboardSnapshot | null }) {
  const warnings = dash?.recent_backend_warnings;
  if (!warnings || warnings.length === 0) return null;
  return (
    <>
      <div className="var-label">Recent backend retries (last {warnings.length})</div>
      <div className="payload-block">
        {warnings.slice().reverse().map((w, i) => {
          const ts = fmtClock(w.ts);
          const code = w.status_code != null ? ` HTTP ${w.status_code}` : "";
          const err = w.error_class ? ` ${w.error_class}` : "";
          const wait = w.wait_s != null ? ` · wait ${Number(w.wait_s).toFixed(1)}s` : "";
          const final = w.final ? " · FINAL" : "";
          const attempts = w.attempt != null ? ` (attempt ${w.attempt}/${w.max_attempts ?? "?"})` : "";
          const q = w.query ? ` · q="${w.query.slice(0, 40)}${w.query.length > 40 ? "…" : ""}"` : "";
          return (
            <div key={i} className="log-row">
              <span className="log-dim">{ts}</span>{" "}
              <span className="log-warn">{w.kind}</span>
              {code}{err}{attempts}{wait}{final}{q}
            </div>
          );
        })}
      </div>
    </>
  );
}
