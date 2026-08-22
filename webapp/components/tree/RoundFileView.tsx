"use client";
import { useState } from "react";
import { Badge, CardFrame } from "@/components/ui";
import type { RoundResult } from "@/lib/api/types";
import { fmtNum, fmtPct1 } from "@/lib/format";
import { isHit } from "@/lib/fitness";

// The round file IS `RoundResult.model_dump()`, so its shape is DERIVED from the generated wire
// type rather than re-declared (`webapp/CLAUDE.md` § A wire shape is GENERATED). A hand-typed
// interface drifts with the gate green, because nothing can compare one against the model it
// claims to describe: require a `hits` the model never declares (`domain/results.py` disowns it
// outright) and every summary line renders "undefined/20 hits" with no check able to say so.
//
// `Partial` because the value reaching this component is a cast over arbitrary parsed JSON: a
// file on disk can promise a SUBSET of the current model, never the whole of it. That is also
// what keeps a removed field a compile error here instead of an `undefined` on screen.
export type RoundDoc = Partial<RoundResult>;

// The one shape that genuinely cannot be derived: `results` is `list[dict[str, Any]]` on the
// model, so the wire type is `Record<string, unknown>[]` and the per-row keys exist nowhere to
// generate from. Hand-written, and saying so, per that section's narrow escape.
interface ResultRow {
  sample_id?: string | number;
  query?: string;
  predicted?: string;
  ground_truth?: string;
  fitness?: number;
}

function fmtTheta(v: number | null | undefined): string {
  return typeof v === "number" ? (v >= 0 ? `+${v.toFixed(3)}` : v.toFixed(3)) : "—";
}

function fmtLift(lift: number | null | undefined, lo: number | null | undefined, hi: number | null | undefined): string {
  if (typeof lift !== "number" || typeof lo !== "number" || typeof hi !== "number") return "—";
  return `${fmtTheta(lift)} [${fmtTheta(lo)}, ${fmtTheta(hi)}]`;
}

interface Props {
  doc: RoundDoc;
  raw: string;
}

export function RoundFileView({ doc, raw }: Props) {
  const [showRaw, setShowRaw] = useState(false);
  const results = (doc.results ?? []) as ResultRow[];
  const scoreboard = doc.scoreboard ?? [];
  // Matched first, full-set only as the fallback, and the label says which — an
  // unlabelled "(parent 18%)" beside a subset accuracy of 58% is a lift nothing measured.
  const matched = typeof doc.matched_parent_accuracy === "number" ? doc.matched_parent_accuracy : null;
  const parentShown = matched ?? (typeof doc.parent_accuracy === "number" ? doc.parent_accuracy : null);
  const parentLabel = matched != null ? "matched parent" : "parent, full set";

  return (
    <div className="round-file-view">
      <div className="round-file-summary">
        <div className="round-file-summary-row">
          <Badge>round {doc.round ?? "—"}</Badge>
          <span>accuracy {fmtPct1(doc.accuracy)} {parentShown != null && (<span style={{ color: "var(--color-text-tertiary)" }} title={matched != null ? "The parent — the origin at round 0, the prior round's winner after — re-scored on the samples this round's winner measured. The floor the promotion gate used." : "The parent's full-set rate. This round carries no matched floor, so it is not directly comparable to a partially-scored winner."}>({parentLabel} {fmtPct1(parentShown)})</span>)}</span>
          <span>composite {fmtNum(doc.composite_fitness)}</span>
          <span>n {doc.total ?? "—"}</span>
          {typeof doc.ability?.theta === "number" && (
            <span title="Ability of the adopted lineage on the cycle's fixed δ ruler — the subset-invariant series the round was won on. The cell count is how much of that ruler was real when this round was read.">
              θ {fmtTheta(doc.ability.theta)}{doc.ability.ruler_n > 0 ? ` (${doc.ability.ruler_n} cells)` : ""}
            </span>
          )}
          {typeof doc.p_value === "number" && <span>p {fmtNum(doc.p_value, 3)}</span>}
          {doc.improved ? <span className="pass">improved</span> : <span style={{ color: "var(--color-text-tertiary)" }}>no improvement</span>}
        </div>
        {doc.verdict_reason && (
          <div className="round-file-summary-row" style={{ color: "var(--color-text-tertiary)" }}>
            {doc.verdict_reason}
          </div>
        )}
      </div>

      {scoreboard.length > 0 && (
        <CardFrame
          style={{ margin: "8px 0" }}
          title={<span>Scoreboard</span>}
          actions={<Badge>{scoreboard.length}</Badge>}
        >
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>#</th>
                  <th>Candidate</th>
                  <th>Accuracy</th>
                  <th>Composite</th>
                  <th title="Difficulty-adjusted Rasch ability on the cycle's fixed δ ruler — the metric the round winner is elected on, which is what explains a lower-accuracy winner. Empty outside the election fit, and for every row while the ruler is cold.">θ</th>
                  <th title="The candidate's blocked lift over the parent on the cells both measured, with its 95% interval. An interval spanning 0 means the round could not separate them.">Lift vs parent</th>
                  <th>Win</th>
                </tr>
              </thead>
              <tbody>
                {scoreboard.map((s, i) => (
                  <tr key={s.candidate_id ?? i}>
                    <td>{s.rank ?? i + 1}</td>
                    <td style={{ maxWidth: 360, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }} title={s.changes_description}>
                      {s.changes_description || s.candidate_id || "—"}
                    </td>
                    <td>{fmtPct1(s.accuracy)}</td>
                    <td>{fmtNum(s.composite_fitness)}</td>
                    <td>{fmtTheta(s.theta)}</td>
                    <td>{fmtLift(s.matched_parent_lift, s.matched_parent_lift_ci_lo, s.matched_parent_lift_ci_hi)}</td>
                    <td>{s.is_winner ? <span className="pass">win</span> : ""}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </CardFrame>
      )}

      {results.length > 0 && (
        <CardFrame
          style={{ margin: "8px 0" }}
          title={<span>Per-sample results</span>}
          actions={<Badge>{results.length}</Badge>}
        >
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th title="Sample ID — stable identifier from the project.">ID</th>
                  <th title="Hit / miss for this sample.">Status</th>
                  <th title="Input given to the pipeline for this sample.">Query</th>
                  <th title="Top-1 prediction returned by the pipeline.">Predicted</th>
                  <th title="Ground-truth answer from the project.">Ground</th>
                </tr>
              </thead>
              <tbody>
                {results.map((r, i) => {
                  const hit = isHit(r.fitness);
                  const id = r.sample_id ?? i;
                  return (
                    <tr key={String(id)}>
                      <td>{String(id)}</td>
                      <td>{hit ? <span className="pass">HIT</span> : <span className="fail">MISS</span>}</td>
                      <td style={{ maxWidth: 280, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }} title={r.query}>
                        {r.query || "—"}
                      </td>
                      <td style={{ maxWidth: 240, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }} title={r.predicted}>
                        {r.predicted || "—"}
                      </td>
                      <td style={{ maxWidth: 160, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }} title={r.ground_truth}>
                        {r.ground_truth || "—"}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </CardFrame>
      )}

      <details open={showRaw} onToggle={(e) => setShowRaw((e.target as HTMLDetailsElement).open)} style={{ margin: "8px 0" }}>
        <summary style={{ cursor: "pointer", padding: "8px 0", color: "var(--color-text-secondary)", fontSize: "var(--text-base)" }}>
          Raw JSON ({raw.length.toLocaleString()} chars)
        </summary>
        <pre style={{ fontFamily: "var(--font-mono)", fontSize: "var(--text-sm)", lineHeight: 1.5, whiteSpace: "pre-wrap", color: "var(--color-text-secondary)", background: "var(--color-background-secondary)", padding: 10, borderRadius: "var(--border-radius-md)", marginTop: 6 }}>
          {raw}
        </pre>
      </details>
    </div>
  );
}
