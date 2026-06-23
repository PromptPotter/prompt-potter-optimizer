"use client";
import { useCallback, useState } from "react";
import { postUnarchiveCampaign, type CampaignSummary } from "@/lib/api";
import { bumpRevalidation } from "@/lib/revalidate";
import { cx } from "@/lib/cx";
import { campaignOriginHash } from "@/lib/ids";
import { campaignDisplayName, unitDisplayName } from "@/lib/names";
import { fmtPct0 } from "@/lib/format";
import { runPhaseLabel } from "@/lib/run-phase";
import { buildUnitTree, type SessionGroup } from "./grouping";
import { UnitBranchRows } from "./UnitBranchRows";
import { CampaignMenu } from "./CampaignMenu";
import { CampaignSizeHover } from "./CampaignSizeHover";

// One session row + (when expanded) its fork-tree. Rendered either AS the
// campaign row (single-session campaign) or as a child session row
// (multi-session campaign) — `isCampaignRow` only changes the label.
export function SessionSubtree({
  campaign,
  session,
  isCampaignRow,
  open,
  onToggle,
  campaignId,
  cycleId,
  activeCampaignId,
  activeCycleId,
  onSelectCycle,
}: {
  campaign: CampaignSummary;
  session: SessionGroup;
  isCampaignRow: boolean;
  open: boolean;
  onToggle: () => void;
  campaignId: string | null;
  cycleId: string | null;
  activeCampaignId: string | null;
  activeCycleId: string | null;
  onSelectCycle: (campaignId: string, cycleId: string) => void;
}) {
  const cid = campaign.campaign_id;
  const root = session.root;
  const hasBranches = session.branches.length > 0;
  const selected = cid === campaignId && root.cycle_id === cycleId;
  // Archived campaigns live in the archive/ recycle bin — inert to browse (their
  // detail routes 404 until restored). So an archived campaign row's primary
  // action is RESTORE, not open: clicking it moves the tree back to campaigns/.
  const archived = isCampaignRow && campaign.lifecycle_status === "archived";
  const [restoring, setRestoring] = useState(false);
  const runRestore = useCallback(async () => {
    setRestoring(true);
    try {
      await postUnarchiveCampaign(cid);
      bumpRevalidation();
    } finally {
      setRestoring(false);
    }
  }, [cid]);
  // A check-in isn't a run, so it never wears the active-pointer ●, even if a stale
  // pointer still names it (it claimed the pointer under the old mint code before the
  // claim moved to Start). The ● tracks the running/last-started cycle the dashboard
  // follows; `selected` already shows what the operator is viewing.
  const active =
    cid === activeCampaignId && root.cycle_id === activeCycleId && root.run_phase !== "checkin";
  const live = root.run_phase === "running";
  // Terminal cycles surface their stop-reason (crashed / max-rounds / target-hit
  // …) so a finished campaign reads as such, not identical to the live one. A
  // paused cycle is non-terminal, so it shows no terminal label. Per-cycle (not
  // the campaign rollup) so multi-session campaigns stay correct.
  const statusLabel =
    !live && root.run_phase === "terminal" ? runPhaseLabel(root.run_phase, root.status) : null;

  // Branch-kind chips (only on the row that owns the fork-tree twist).
  const counts = { fork: 0, sweep: 0, diag: 0 };
  for (const b of session.branches) {
    if (b.sibling_kind in counts) counts[b.sibling_kind as keyof typeof counts] += 1;
  }
  const chips = [
    { kind: "fork", glyph: "⑂", count: counts.fork },
    { kind: "sweep", glyph: "~", count: counts.sweep },
    { kind: "diag", glyph: "Δ", count: counts.diag },
  ].filter((c) => c.count > 0);

  const label = isCampaignRow ? campaignDisplayName(campaign) : unitDisplayName(root);

  const familyRow = (
    <div className={cx("unit-library-family", selected && "selected", archived && "archived")}>
      <button
          type="button"
          className="unit-library-twist"
          onClick={onToggle}
          aria-label={open ? "Collapse" : "Expand"}
          aria-expanded={open}
          disabled={!hasBranches}
          tabIndex={-1}
        >
          {!hasBranches ? "" : open ? "▼" : "▶"}
        </button>
        <button
          type="button"
          className="unit-library-item"
          onClick={() => (archived ? void runRestore() : onSelectCycle(cid, root.cycle_id))}
          aria-current={selected ? "true" : undefined}
          title={archived ? "Archived — click to restore" : isCampaignRow ? cid : root.cycle_id}
          disabled={restoring}
        >
          <span className="unit-library-mark">{active ? "●" : ""}</span>
          <span className="unit-library-row">
            <span className="unit-library-name">
              {label}
              {isCampaignRow && !campaign.label && (
                <span className="unit-library-hash" title={cid}>
                  #{campaignOriginHash(cid).slice(0, 6)}
                </span>
              )}
              {live && (
                <span className="unit-library-live" title="Status is running">
                  ●
                </span>
              )}
            </span>
            <span className="unit-library-meta">
              {archived ? (
                <span className="unit-library-status">
                  {restoring ? "Restoring…" : "Archived · restore"}
                </span>
              ) : (
                <>
                  {statusLabel && (
                    <>
                      <span className="unit-library-status">{statusLabel}</span>
                      {" · "}
                    </>
                  )}
                  {fmtPct0(session.bestAccuracy)}
                </>
              )}
            </span>
          </span>
        </button>
        {chips.length > 0 && (
          <span className="unit-library-chips">
            {chips.map((chip) => (
              <button
                key={chip.kind}
                type="button"
                className="unit-library-chip"
                onClick={onToggle}
                title={`${chip.count} ${chip.kind}${chip.count === 1 ? "" : "s"}`}
                aria-label={`${chip.count} ${chip.kind}`}
                tabIndex={-1}
              >
                <span className="unit-library-chip-glyph" aria-hidden="true">
                  {chip.glyph}
                </span>
                <span className="unit-library-chip-count">{chip.count}</span>
              </button>
            ))}
          </span>
        )}
        {isCampaignRow && <CampaignMenu campaign={campaign} />}
    </div>
  );

  return (
    <>
      {isCampaignRow ? (
        <CampaignSizeHover campaignId={cid}>{familyRow}</CampaignSizeHover>
      ) : (
        familyRow
      )}
      {open && hasBranches && (
        <ul className="unit-library-children">
          <UnitBranchRows
            nodes={buildUnitTree(root, session.branches).children}
            campaignId={campaignId}
            cycleId={cycleId}
            activeCampaignId={activeCampaignId}
            activeCycleId={activeCycleId}
            onSelectCycle={onSelectCycle}
          />
        </ul>
      )}
    </>
  );
}
