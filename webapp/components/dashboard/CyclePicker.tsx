"use client";
import { memo, useMemo } from "react";
import { useWorkspace } from "@/lib/workspace";
import { useCycleStream } from "@/lib/poll";
import type { CycleListEntry } from "@/lib/api";
import { rootCycleId, sessionIndexOf, campaignOriginHash, unitKey, UNIT_SEP } from "@/lib/ids";
import { unitDisplayName } from "@/lib/names";
import { fmtPct0 } from "@/lib/format";

// Inline unit picker. Single label scheme — optgroup `${dataset} ·
// ${shortHash}`, option `${unit} · best X · status`. The `variant` prop
// only switches the wrapper CSS class (and the empty-state copy) so the
// chat header can size the trigger differently from the dashboard
// breadcrumb; the labels themselves are identical in both contexts.
//
// A `cycle_id` is unique only within its campaign, so every option is keyed
// and valued by the composite `campaign_id::cycle_id` (unitKey); selecting
// one passes BOTH ids back.

function optionText(c: CycleListEntry): string {
  return `${unitDisplayName(c)} · best ${fmtPct0(c.best_accuracy)} · ${c.status}`;
}

// The live badge subscribes to the 2 s dashboard.json poll on its own. Kept
// out of the picker body so the picker's heavy optgroup tree doesn't rebuild
// on every tick.
function LiveBadge() {
  const { isLive } = useCycleStream();
  if (!isLive) return null;
  return (
    <span
      className="live-badge"
      title="Campaign is actively running — dashboard updated in the last 60s"
    >
      ● Live
    </span>
  );
}

export const CyclePicker = memo(function CyclePicker({
  variant = "breadcrumb",
}: {
  variant?: "breadcrumb" | "standalone";
}) {
  const {
    cycleId,
    campaignId,
    cycles,
    cyclesLoaded,
    cyclesError,
    activeCycleId,
    activeCampaignId,
    following,
    selectCycle,
    followActive,
  } = useWorkspace();

  const standalone = variant === "standalone";

  // Group + sort lives in a useMemo keyed on `cycles` so the O(N log N) work
  // only fires when the workspace poll actually mutates the list.
  const { groups, groupKeys } = useMemo(() => {
    const g = new Map<string, CycleListEntry[]>();
    for (const c of cycles) {
      const arr = g.get(c.campaign_id) ?? [];
      arr.push(c);
      g.set(c.campaign_id, arr);
    }
    for (const arr of g.values()) {
      arr.sort((a, b) => {
        const sa = sessionIndexOf(rootCycleId(a.cycle_id));
        const sb = sessionIndexOf(rootCycleId(b.cycle_id));
        if (sa !== sb) return sa - sb;
        if (a.is_root !== b.is_root) return a.is_root ? -1 : 1;
        return a.updated_at < b.updated_at ? 1 : -1;
      });
    }
    const lastTouched = (entries: CycleListEntry[]) =>
      Math.max(...entries.map((c) => Date.parse(c.updated_at) || 0));
    const keys = [...g.keys()].sort(
      (a, b) => lastTouched(g.get(b)!) - lastTouched(g.get(a)!),
    );
    return { groups: g, groupKeys: keys };
  }, [cycles]);

  if (cyclesError && cycles.length === 0) {
    return <span className="cycle-picker-err">campaigns: {cyclesError}</span>;
  }
  if (!cyclesLoaded) {
    return <span>{standalone ? "New Job" : cycleId || "loading…"}</span>;
  }
  if (cycles.length === 0) {
    return <span>{standalone ? "New Job" : cycleId || "No campaigns yet"}</span>;
  }

  const currentKey = campaignId && cycleId ? unitKey(campaignId, cycleId) : "";
  const selectedKnown = cycles.some(
    (c) => c.campaign_id === campaignId && c.cycle_id === cycleId,
  );

  return (
    <span className={`cycle-picker${standalone ? " standalone" : ""}`}>
      <select
        value={currentKey}
        onChange={(e) => {
          const idx = e.target.value.indexOf(UNIT_SEP);
          if (idx < 0) return;
          selectCycle(
            e.target.value.slice(0, idx),
            e.target.value.slice(idx + UNIT_SEP.length),
          );
        }}
        aria-label="Switch campaign or session"
      >
        {!selectedKnown && currentKey && (
          <option value={currentKey} disabled>
            {cycleId} (not on disk)
          </option>
        )}
        {groupKeys.map((cid) => {
          const entries = groups.get(cid)!;
          const dataset = entries[0]?.dataset_name || "(unknown)";
          const groupLabel = `${dataset} · ${campaignOriginHash(cid).slice(0, 6)}`;
          return (
            <optgroup key={cid} label={groupLabel}>
              {entries.map((c) => {
                const k = unitKey(c.campaign_id, c.cycle_id);
                const isActiveOption =
                  c.campaign_id === activeCampaignId &&
                  c.cycle_id === activeCycleId;
                return (
                  <option key={k} value={k}>
                    {isActiveOption ? "● " : ""}
                    {optionText(c)}
                  </option>
                );
              })}
            </optgroup>
          );
        })}
      </select>
      {!following && (
        <button
          type="button"
          className="follow-active-btn"
          onClick={followActive}
          title="Pinned to this unit. Click to resume following the campaign the CLI is currently running."
        >
          ↪ Follow active
        </button>
      )}
      <LiveBadge />
    </span>
  );
});
