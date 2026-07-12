"use client";
import { useState } from "react";
import { Badge, CardFrame } from "@/components/ui";
import { RotatePrompt } from "@/components/shell/RotatePrompt";
import { fmtNum, fmtPct1 } from "@/lib/format";

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
  hit?: boolean;
}

export interface RoundDoc {
  round?: number;
  accuracy?: number;
  composite_fitness?: number;
  hits: number;
  total: number;
  origin_accuracy?: number;
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

  return (
    <RotatePrompt surfaceName="The round file view">
    <div className="round-file-view">
      <div className="round-file-summary">
        <div className="round-file-summary-row">
          <Badge>round {doc.round ?? "—"}</Badge>
          <span>accuracy {fmtPct1(doc.accuracy)} {typeof doc.origin_accuracy === "number" && (<span style={{ color: "var(--color-text-tertiary)" }}>(origin {fmtPct1(doc.origin_accuracy)})</span>)}</span>
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
                  const hit = r.hit ?? false;
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
