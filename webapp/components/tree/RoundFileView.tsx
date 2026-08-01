"use client";
import { useState } from "react";
import { Badge, CardFrame } from "@/components/ui";
import { RotatePrompt } from "@/components/shell/RotatePrompt";
import { fmtNum, fmtPct1 } from "@/lib/format";
import { isHit } from "@/lib/fitness";

interface ScoreboardEntry {
  rank?: number;
  candidate_id?: string;
  changes_description?: string;
  accuracy?: number;
  composite_fitness?: number;
  hits: number;
  total: number;
  is_winner?: boolean;
}

interface ResultRow {
  sample_id?: string | number;
  query?: string;
  predicted?: string;
  ground_truth?: string;
  fitness?: number;
}

export interface RoundDoc {
  round?: number;
  accuracy?: number;
  composite_fitness?: number;
  hits: number;
  total: number;
  origin_accuracy?: number;
  // The origin restricted to the winner's OWN measured samples — the floor `improved`
  // was decided against. Preferred over `origin_accuracy` below: under elimination the
  // winner may have run 8 of 20 samples, and quoting the full-set rate beside its
  // subset accuracy renders a lift that was never measured.
  matched_origin_accuracy?: number | null;
  improved?: boolean;
  p_value?: number;
  scoreboard?: ScoreboardEntry[];
  results?: ResultRow[];
}

interface Props {
  doc: RoundDoc;
  raw: string;
}

export function RoundFileView({ doc, raw }: Props) {
  const [showRaw, setShowRaw] = useState(false);
  const results = doc.results ?? [];
  const scoreboard = doc.scoreboard ?? [];
  // Matched first, full-set only as the fallback, and the label says which — an
  // unlabelled "(origin 18%)" beside a subset accuracy of 58% is a lift nothing measured.
  const matched = typeof doc.matched_origin_accuracy === "number" ? doc.matched_origin_accuracy : null;
  const originShown = matched ?? (typeof doc.origin_accuracy === "number" ? doc.origin_accuracy : null);
  const originLabel = matched != null ? "matched origin" : "origin, full set";

  return (
    <RotatePrompt surfaceName="The round file view">
    <div className="round-file-view">
      <div className="round-file-summary">
        <div className="round-file-summary-row">
          <Badge>round {doc.round ?? "—"}</Badge>
          <span>accuracy {fmtPct1(doc.accuracy)} {originShown != null && (<span style={{ color: "var(--color-text-tertiary)" }} title={matched != null ? "The origin re-scored on the samples this round's winner measured — the floor the promotion gate used." : "The origin's full-set rate. This round carries no matched floor, so it is not directly comparable to a partially-scored winner."}>({originLabel} {fmtPct1(originShown)})</span>)}</span>
          <span>composite {fmtNum(doc.composite_fitness)}</span>
          <span>{doc.hits}/{doc.total} hits</span>
          {typeof doc.p_value === "number" && <span>p {fmtNum(doc.p_value, 3)}</span>}
          {doc.improved ? <span className="pass">improved</span> : <span style={{ color: "var(--color-text-tertiary)" }}>no improvement</span>}
        </div>
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
                  <th>Hits</th>
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
                    <td>{s.hits}/{s.total}</td>
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
        <summary style={{ cursor: "pointer", padding: "8px 0", color: "var(--color-text-secondary)", fontSize: 13 }}>
          Raw JSON ({raw.length.toLocaleString()} chars)
        </summary>
        <pre style={{ fontFamily: "var(--font-mono)", fontSize: 12, lineHeight: 1.5, whiteSpace: "pre-wrap", color: "var(--color-text-secondary)", background: "var(--color-background-secondary)", padding: 10, borderRadius: "var(--border-radius-md)", marginTop: 6 }}>
          {raw}
        </pre>
      </details>
    </div>
    </RotatePrompt>
  );
}
