"use client";
import type { CycleListEntry } from "@/lib/api";
import { shortFamilyTail } from "@/lib/ids";
import { fmtPct0 } from "@/lib/format";
import { UNIT_KIND_LABEL } from "@/lib/names";
import type { UnitNode } from "./grouping";

// The fork-tree's branch rows — every fork / diag / sweep, nested under
// its parent. The session root is NOT re-rendered here (the session row
// above is the root).
export function UnitBranchRows({
  nodes,
  campaignId,
  cycleId,
  activeCampaignId,
  activeCycleId,
  onSelectCycle,
}: {
  nodes: UnitNode[];
  campaignId: string | null;
  cycleId: string | null;
  activeCampaignId: string | null;
  activeCycleId: string | null;
  onSelectCycle: (campaignId: string, cycleId: string) => void;
}) {
  return (
    <>
      {nodes.map((node) => {
        const u = node.unit;
        return (
          <li key={u.cycle_id}>
            <UnitRow
              unit={u}
              selected={u.campaign_id === campaignId && u.cycle_id === cycleId}
              active={
                u.campaign_id === activeCampaignId && u.cycle_id === activeCycleId
              }
              onSelect={() => onSelectCycle(u.campaign_id, u.cycle_id)}
            />
            {node.children.length > 0 && (
              <ul className="unit-library-children">
                <UnitBranchRows
                  nodes={node.children}
                  campaignId={campaignId}
                  cycleId={cycleId}
                  activeCampaignId={activeCampaignId}
                  activeCycleId={activeCycleId}
                  onSelectCycle={onSelectCycle}
                />
              </ul>
            )}
          </li>
        );
      })}
    </>
  );
}

// One branch row inside a session's fork-tree — a fork / diag / sweep,
// carrying its kind badge and the disambiguating id tail.
function UnitRow({
  unit,
  selected,
  active,
  onSelect,
}: {
  unit: CycleListEntry;
  selected: boolean;
  active: boolean;
  onSelect: () => void;
}) {
  const live = unit.run_phase === "running";
  const kindLabel =
    unit.unit_kind === "session" ? null : UNIT_KIND_LABEL[unit.unit_kind];
  return (
    <button
      type="button"
      className={`unit-library-item unit-library-child${selected ? " selected" : ""}`}
      onClick={onSelect}
      aria-current={selected ? "true" : undefined}
      title={unit.cycle_id}
    >
      <span className="unit-library-mark">{active ? "●" : ""}</span>
      <span className="unit-library-row">
        <span className="unit-library-name">
          {shortFamilyTail(unit.cycle_id)}
          {kindLabel != null && (
            <span className="unit-library-kind" title={`This unit is a ${kindLabel}`}>
              {kindLabel}
            </span>
          )}
          {live && <span className="unit-library-live" title="Unit status is running">●</span>}
        </span>
        <span className="unit-library-meta">{fmtPct0(unit.best_accuracy)}</span>
      </span>
    </button>
  );
}
