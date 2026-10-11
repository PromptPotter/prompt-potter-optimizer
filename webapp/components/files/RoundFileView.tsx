"use client";
import { useState } from "react";
import { Badge, CardFrame, Term } from "@/components/ui";
import type { PairedReading, RoundResult } from "@/lib/api/types";
import { fmtNum, fmtPct1, fmtSigned } from "@/lib/format";
import { PairedLift } from "@/components/shell/PairedLift";

function ArmLift({ reading }: { reading: PairedReading | null }) {
  if (reading === null) return <>—</>;
  return (
    <PairedLift reading={reading}>
      {({ estimate }) =>
        `${fmtSigned(estimate.value, 3)} [${fmtSigned(estimate.ci_lo, 3)}, ${fmtSigned(estimate.ci_hi, 3)}]`
      }
    </PairedLift>
  );
}

interface Props {
  doc: RoundResult;
  raw: string;
}

export function RoundFileView({ doc, raw }: Props) {
  const [showRaw, setShowRaw] = useState(false);
  const { results, scoreboard } = doc;
  const wonOnTheta = doc.elects_on === "ability";

  return (
    <div className="round-file-view">
      <div className="round-file-summary">
        <div className="round-file-summary-row">
          <Badge className="round-file-badge">round {doc.round}</Badge>
          <span>accuracy {fmtPct1(doc.accuracy)}</span>
          <span>composite {fmtNum(doc.composite_fitness)}</span>
          <span>n {doc.total}</span>
          {typeof doc.ability?.theta === "number" && (
            <Term
              content={`Ability of the adopted lineage on the cycle's fixed δ ruler — the subset-invariant series${wonOnTheta ? " the round was won on" : ""}. The cell count is how much of that ruler was real when this round was read.`}
            >
              θ {fmtSigned(doc.ability.theta, 3)}{doc.ability.ruler_n > 0 ? ` (${doc.ability.ruler_n} cells)` : ""}
            </Term>
          )}
          {doc.improved ? <span className="pass">improved</span> : <span className="round-file-dim">no improvement</span>}
        </div>
        {doc.verdict_reason && (
          <div className="round-file-summary-row round-file-dim">{doc.verdict_reason}</div>
        )}
      </div>

      {scoreboard.length > 0 && (
        <CardFrame title={<span>Scoreboard</span>} actions={<Badge>{scoreboard.length}</Badge>}>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>#</th>
                  <th>Candidate</th>
                  <th>Accuracy</th>
                  <th>Composite</th>
                  <th><Term content={`Difficulty-adjusted Rasch ability on the cycle's fixed δ ruler${wonOnTheta ? " — the metric the round winner is elected on, which is what explains a lower-accuracy winner" : ""}. Empty outside the election fit, and for every row while the ruler is cold.`}>θ</Term></th>
                  <th><Term content="The candidate's blocked lift over the parent on the cells both measured, with its 95% interval. An interval spanning 0 means the round could not separate them.">Lift vs parent</Term></th>
                  <th>Win</th>
                </tr>
              </thead>
              <tbody>
                {scoreboard.map(({ rank, reading: s }, i) => (
                  <tr key={s.arm.candidate_id || i}>
                    <td>{rank ?? i + 1}</td>
                    <td className="round-file-clip wide" title={s.changes_description}>
                      {s.changes_description || s.arm.candidate_id || "—"}
                    </td>
                    <td>{fmtPct1(s.own?.accuracy?.value)}</td>
                    <td>{fmtNum(s.own?.composite?.value)}</td>
                    <td>{fmtSigned(s.ability?.theta, 3)}</td>
                    <td>
                      <ArmLift reading={s.vs_reference} />
                    </td>
                    <td>{s.election.selected ? <span className="pass">win</span> : ""}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </CardFrame>
      )}

      {results.length > 0 && (
        <CardFrame title={<span>Per-sample results</span>} actions={<Badge>{results.length}</Badge>}>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th><Term content="Sample ID — stable identifier from the project.">ID</Term></th>
                  <th><Term content="The graded score this file carries for the sample. The file holds no mark: an errored row reads 0 here, and the round's own view says which rows those are.">Fitness</Term></th>
                  <th><Term content="Input given to the pipeline for this sample.">Query</Term></th>
                  <th><Term content="Top-1 prediction returned by the pipeline.">Predicted</Term></th>
                  <th><Term content="Ground-truth answer from the project.">Ground</Term></th>
                </tr>
              </thead>
              <tbody>
                {results.map((r, i) => (
                  // A cell measured twice is two rows of one sample.
                  <tr key={`${r.sample_id}:${i}`}>
                    <td>{r.sample_id}</td>
                    <td>{fmtNum(r.fitness)}</td>
                    <td className="round-file-clip" title={r.query}>
                      {r.query || "—"}
                    </td>
                    <td className="round-file-clip" title={r.predicted}>
                      {r.predicted || "—"}
                    </td>
                    <td className="round-file-clip" title={r.ground_truth}>
                      {r.ground_truth || "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </CardFrame>
      )}

      <details
        className="round-file-raw"
        open={showRaw}
        onToggle={(e) => setShowRaw((e.target as HTMLDetailsElement).open)}
      >
        <summary>Raw JSON ({raw.length.toLocaleString()} chars)</summary>
        <pre>{raw}</pre>
      </details>
    </div>
  );
}
