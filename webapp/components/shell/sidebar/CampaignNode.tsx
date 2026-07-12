"use client";
import { useState } from "react";
import { cx } from "@/lib/cx";
import { campaignDisplayName } from "@/lib/names";
import { fmtPct0 } from "@/lib/format";
import { runPhaseLabel } from "@/lib/run-phase";
import { isSelfOptimization } from "@/lib/derivations";
import { buildUnitTree, type CampaignGroup } from "./grouping";
import { UnitBranchRows } from "./UnitBranchRows";
import { CampaignMenu } from "./CampaignMenu";
import { CampaignSizeHover } from "./CampaignSizeHover";
import { InnerCampaignRows } from "./InnerCampaignRows";

// A chip's tooltip has to say what the branch IS — "2 diags" only decodes the glyph
// for someone who already knows the vocabulary, which a first-time operator doesn't.
const CHIP_HELP: Record<string, string> = {
  fork: "a sibling run branched off this one to try a different course",
  sweep: "a cheap A/B of candidate prompts, run before committing to one",
  diag: "a diagnostic re-run: replays the same work to check the result holds",
};

// One campaign in the flat list: its row + (when expanded) its fork-tree.
// The campaign row IS its root cycle — a campaign mints exactly one — so the
// row opens that cycle directly and its twist expands the root's forks.
export function CampaignNode({
  group,
  collapsedNodes,
  toggleNode,
  campaignId,
  cycleId,
  activeCampaignId,
  activeCycleId,
  onSelectCycle,
}: {
  group: CampaignGroup;
  collapsedNodes: Set<string>;
  toggleNode: (key: string) => void;
  campaignId: string | null;
  cycleId: string | null;
  activeCampaignId: string | null;
  activeCycleId: string | null;
  onSelectCycle: (campaignId: string, cycleId: string) => void;
}) {
  const { campaign, root, branches } = group;
  const cid = campaign.campaign_id;
  const cmpKey = `cmp:${cid}`;
  const open = !collapsedNodes.has(cmpKey);
  const onToggle = () => toggleNode(cmpKey);
  const hasBranches = branches.length > 0;
  // L4: this campaign optimizes the meta-prompts, so its root cycle spawned a
  // fan-out of inner campaigns — reachable from the `↻ inner` chip.
  // Keyed on the connector KIND, never on a dataset name: ANY dataset whose
  // `pipeline.json::backend_type` is `promptpotter` recurses, including an
  // operator's own pp-self variant. Same predicate the panels branch on.
  const isL4 = isSelfOptimization(campaign.backend_type);
  const [innerOpen, setInnerOpen] = useState(false);
  const selected = cid === campaignId && root.cycle_id === cycleId;
  // Archived campaigns live in the archive/ recycle bin — inert to browse (their detail
  // routes 404 until restored), so the row is inert too. Restoring is a lifecycle verb and
  // the ⋯ menu owns those (it already offers Unarchive beside Archive + Delete) — a bare
  // row click firing an unconfirmed, undoable mutation was a second path to the same call.
  const archived = campaign.lifecycle_status === "archived";
  // A check-in isn't a run, so it never wears the active-pointer ●, even if a stale
  // pointer still names it (it claimed the pointer under the old mint code before the
  // claim moved to Start). The ● tracks the running/last-started cycle the dashboard
  // follows; `selected` already shows what the operator is viewing.
  const active =
    cid === activeCampaignId && root.cycle_id === activeCycleId && root.run_phase !== "checkin";
  const live = root.run_phase === "running";
  // Terminal cycles surface their stop-reason (crashed / max-rounds / target-hit
  // …) so a finished campaign reads as such, not identical to the live one. A
  // paused cycle is non-terminal, so it shows no terminal label.
  const statusLabel =
    !live && root.run_phase === "terminal" ? runPhaseLabel(root.run_phase, root.status) : null;

  // Branch-kind chips.
  const counts = { fork: 0, sweep: 0, diag: 0 };
  for (const b of branches) {
    if (b.sibling_kind in counts) counts[b.sibling_kind as keyof typeof counts] += 1;
  }
  const chips = [
    { kind: "fork", glyph: "⑂", count: counts.fork },
    { kind: "sweep", glyph: "~", count: counts.sweep },
    { kind: "diag", glyph: "Δ", count: counts.diag },
  ].filter((c) => c.count > 0);

  return (
    <>
      <CampaignSizeHover campaignId={cid}>
        <div className={cx("unit-library-family", selected && "selected", archived && "archived")}>
          {hasBranches && (
            <button
              type="button"
              className="unit-library-twist"
              onClick={onToggle}
              aria-label={open ? "Collapse" : "Expand"}
              aria-expanded={open}
              tabIndex={-1}
            >
              {open ? "▼" : "▶"}
            </button>
          )}
          <button
            type="button"
            className="unit-library-item"
            onClick={() => onSelectCycle(cid, root.cycle_id)}
            aria-current={selected ? "true" : undefined}
            title={archived ? "Archived — restore it from the ⋯ menu to open" : cid}
            disabled={archived}
          >
            <span className="unit-library-row">
              <span className="unit-library-name">
                {campaignDisplayName(campaign)}
                {live ? (
                  <span className="unit-library-live" title="Status is running">
                    ●
                  </span>
                ) : active ? (
                  <span className="unit-library-live active" title="Dashboard follows this run">
                    ●
                  </span>
                ) : null}
              </span>
              <span className="unit-library-meta">
                {archived ? (
                  <span className="unit-library-status">Archived</span>
                ) : (
                  <>
                    {statusLabel && (
                      <>
                        <span className="unit-library-status">{statusLabel}</span>
                        {" · "}
                      </>
                    )}
                    {fmtPct0(group.bestAccuracy)}
                  </>
                )}
              </span>
            </span>
          </button>
          {(chips.length > 0 || isL4) && (
            <span className="unit-library-chips">
              {chips.map((chip) => (
                <button
                  key={chip.kind}
                  type="button"
                  className="unit-library-chip"
                  onClick={onToggle}
                  title={`${chip.count} ${chip.kind}${chip.count === 1 ? "" : "s"} — ${CHIP_HELP[chip.kind]}`}
                  aria-label={`${chip.count} ${chip.kind}`}
                  tabIndex={-1}
                >
                  <span className="unit-library-chip-glyph" aria-hidden="true">
                    {chip.glyph}
                  </span>
                  <span className="unit-library-chip-count">{chip.count}</span>
                </button>
              ))}
              {isL4 && (
                <button
                  type="button"
                  className={cx("unit-library-chip", innerOpen && "open")}
                  onClick={() => setInnerOpen((o) => !o)}
                  aria-expanded={innerOpen}
                  title="Inner campaigns this L4 cycle ran (one per candidate)"
                >
                  <span className="unit-library-chip-glyph" aria-hidden="true">
                    ↻
                  </span>
                  <span className="unit-library-chip-count">inner</span>
                </button>
              )}
            </span>
          )}
          <CampaignMenu campaign={campaign} />
        </div>
      </CampaignSizeHover>
      {open && hasBranches && (
        <ul className="unit-library-children">
          <UnitBranchRows
            nodes={buildUnitTree(root, branches).children}
            campaignId={campaignId}
            cycleId={cycleId}
            activeCampaignId={activeCampaignId}
            activeCycleId={activeCycleId}
            onSelectCycle={onSelectCycle}
          />
        </ul>
      )}
      {isL4 && innerOpen && (
        <ul className="unit-library-children inner-library">
          <InnerCampaignRows outerCampaignId={cid} outerCycleId={root.cycle_id} />
        </ul>
      )}
    </>
  );
}
