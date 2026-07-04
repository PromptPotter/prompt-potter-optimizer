"use client";
// Champion Console — the ranked table of candidate meta-prompt states, the
// developer answer to "which meta-prompt is overall best?". Ranks by the
// anchor-to-origin paired effect (cheap, always-available, provisional);
// confirmation is earned by a coronation match (the row ops, wired in the
// champion-selection slice). Reads the server-reduced registry; no recompute.

import type { ChampionCandidate, ChampionRegistryResponse } from "@/lib/api";
import { CardFrame, Badge, type BadgeTone } from "@/components/ui";
import { cx } from "@/lib/cx";

const STATUS_TONE: Record<string, BadgeTone> = {
  champion: "success",
  confirmed: "success",
  provisional: "accent",
};

function effectClass(row: ChampionCandidate): string {
  // Colour the sign, but the number + CI carry the meaning (color is never alone).
  if (row.ci_lo > 0) return "lab-eff-pos";
  if (row.ci_hi < 0) return "lab-eff-neg";
  return "lab-eff-flat"; // CI spans zero — indistinguishable from origin
}

function fmt(n: number): string {
  return (n >= 0 ? "+" : "") + n.toFixed(4);
}

/** One preset data-collection operation — inert until the coronation slice lands. */
function OpButton({ label, title }: { label: string; title: string }) {
  return (
    <button type="button" className="lab-op" disabled title={`${title} (coming)`}>
      {label}
    </button>
  );
}

function ChampionRow({
  row,
  rank,
  onOpenCycle,
}: {
  row: ChampionCandidate;
  rank: number;
  onOpenCycle?: (campaignId: string, cycleId: string) => void;
}) {
  const prov = row.provenance[0];
  return (
    <tr className={cx("lab-row", row.status === "champion" && "lab-row-champion")}>
      <td className="lab-rank">{rank}</td>
      <td className="lab-state">
        <code>{row.state_hash}</code>
        <Badge tone={STATUS_TONE[row.status] ?? "default"}>{row.status}</Badge>
      </td>
      <td className={cx("lab-effect", effectClass(row))}>
        <span className="lab-effect-mean">{fmt(row.anchor_effect)}</span>
        <span className="lab-effect-ci">
          [{fmt(row.ci_lo)}, {fmt(row.ci_hi)}]
        </span>
      </td>
      <td className="lab-num">
        {row.n_cells}
        <span className="lab-dim"> / {row.n_measurements}</span>
      </td>
      <td className="lab-num">
        {row.provenance.length}
        {prov && onOpenCycle ? (
          <button
            type="button"
            className="lab-open"
            title={`Open ${prov.campaign_id} · ${prov.cycle_id}`}
            onClick={() => onOpenCycle(prov.campaign_id, prov.cycle_id)}
          >
            open
          </button>
        ) : null}
      </td>
      <td className="lab-label" title={row.label}>
        {row.label}
      </td>
      <td className="lab-ops">
        <OpButton label="Coronation" title="Head-to-head paired match vs the reigning champion" />
        <OpButton label="+Seeds" title="Collect more seeds on this state's cells" />
        <OpButton label="+Cells" title="Measure this state on more in-band cells" />
      </td>
    </tr>
  );
}

export function ChampionConsole({
  registry,
  onOpenCycle,
}: {
  registry: ChampionRegistryResponse;
  onOpenCycle?: (campaignId: string, cycleId: string) => void;
}) {
  const rows = registry.candidates;
  return (
    <CardFrame
      title="Champion table"
      headingTag="h2"
      actions={
        <span className="lab-subtle">
          {rows.length} state{rows.length === 1 ? "" : "s"} · {registry.n_cycles_scanned} cycles
        </span>
      }
    >
      <p className="lab-lede">
        Every candidate meta-prompt state on disk, ranked by anchor-to-origin effect (paired
        candidate−origin over shared cells). Ranking is <strong>provisional</strong>; a coronation
        match <strong>confirms</strong> a state against the reigning champion.
      </p>
      {rows.length === 0 ? (
        <p className="lab-empty">
          No candidate meta-prompt states yet — run <code>promptpotter new promptpotter-self</code>{" "}
          to produce some, then reopen this tab.
        </p>
      ) : (
        <div className="lab-table-wrap">
          <table className="lab-table">
            <thead>
              <tr>
                <th scope="col">#</th>
                <th scope="col">state</th>
                <th scope="col">anchor effect · 95% CI</th>
                <th scope="col">cells / n</th>
                <th scope="col">seen</th>
                <th scope="col">edit</th>
                <th scope="col">collect</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row, i) => (
                <ChampionRow
                  key={row.state_hash}
                  row={row}
                  rank={i + 1}
                  onOpenCycle={onOpenCycle}
                />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </CardFrame>
  );
}
