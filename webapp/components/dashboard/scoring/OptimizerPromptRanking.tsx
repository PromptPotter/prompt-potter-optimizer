"use client";
// The ranked table of edits to the optimizer's OWN prompts — the
// developer answer to "which meta-prompt is overall best?". Ranks by the
// anchor-to-origin paired effect. Reads the server-reduced registry; no recompute.
// Renders only for the outer pp-self loop (DashboardTab's isOuterSelfOpt gate).
// Ranking is the whole surface: nothing here crowns a winner or graduates one into
// promptpotter/assets/optimizer — that is a deliberate hand-edit.

import type { RankedOptimizerPrompt, OptimizerPromptRanking } from "@/lib/api";
import { CardFrame } from "@/components/ui";
import { cx } from "@/lib/cx";

function effectClass(row: RankedOptimizerPrompt): string {
  // Colour the sign, but the number + CI carry the meaning (color is never alone).
  if (row.ci_lo > 0) return "l4-eff-pos";
  if (row.ci_hi < 0) return "l4-eff-neg";
  return "l4-eff-flat"; // CI spans zero — indistinguishable from origin
}

function fmt(n: number): string {
  return (n >= 0 ? "+" : "") + n.toFixed(4);
}

function RankedPromptRow({
  row,
  rank,
}: {
  row: RankedOptimizerPrompt;
  rank: number;
}) {
  return (
    <tr className="l4-row">
      <td className="l4-rank">{rank}</td>
      <td className="l4-state">
        <code>{row.state_hash}</code>
      </td>
      <td className={cx("l4-effect", effectClass(row))}>
        <span className="l4-effect-mean">{fmt(row.anchor_effect)}</span>
        <span className="l4-effect-ci">
          [{fmt(row.ci_lo)}, {fmt(row.ci_hi)}]
        </span>
      </td>
      <td className="l4-num">
        {row.n_cells}
        <span className="l4-dim"> / {row.n_measurements}</span>
      </td>
      <td className="l4-num">{row.provenance.length}</td>
      <td className="l4-label" title={row.label}>
        {row.label}
      </td>
    </tr>
  );
}

export function OptimizerPromptRankingPanel({ registry }: { registry: OptimizerPromptRanking }) {
  const rows = registry.candidates;
  return (
    <CardFrame
      title="Optimizer-prompt ranking"
      headingTag="h2"
      actions={
        <span className="l4-subtle">
          {rows.length} state{rows.length === 1 ? "" : "s"} · {registry.n_cycles_scanned} cycles
        </span>
      }
    >
      <p className="l4-lede">
        Every candidate meta-prompt state on disk, ranked by anchor-to-origin effect (paired
        candidate−origin over shared cells). Absolute scores across runs are not comparable
        — only these paired effects are.
      </p>
      {rows.length === 0 ? (
        <p className="l4-empty">
          No candidate meta-prompt states yet — run <code>promptpotter new promptpotter-self</code>{" "}
          to produce some.
        </p>
      ) : (
        <div className="l4-table-wrap">
          <table className="l4-table">
            <thead>
              <tr>
                <th scope="col">#</th>
                <th scope="col">state</th>
                <th scope="col">anchor effect · 95% CI</th>
                <th scope="col">cells / n</th>
                <th scope="col">seen</th>
                <th scope="col">edit</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row, i) => (
                <RankedPromptRow key={row.state_hash} row={row} rank={i + 1} />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </CardFrame>
  );
}
