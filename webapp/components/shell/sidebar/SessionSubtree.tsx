"use client";
import type { CampaignSummary } from "@/lib/api";
import { campaignOriginHash } from "@/lib/ids";
import { campaignDisplayName, unitDisplayName } from "@/lib/names";
import { fmtPct0 } from "@/lib/format";
import { buildUnitTree, type SessionGroup } from "./grouping";
import { UnitBranchRows } from "./UnitBranchRows";

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
  const active = cid === activeCampaignId && root.cycle_id === activeCycleId;
  const status = root.status;
  const live = status === "running" || status === "optimizing";

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

  return (
    <>
      <div className={`unit-library-family${selected ? " selected" : ""}`}>
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
          onClick={() => onSelectCycle(cid, root.cycle_id)}
          aria-current={selected ? "true" : undefined}
          title={isCampaignRow ? cid : root.cycle_id}
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
            <span className="unit-library-meta">{fmtPct0(root.best_accuracy)}</span>
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
      </div>
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
