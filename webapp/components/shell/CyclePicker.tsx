"use client";
import { memo, useMemo } from "react";
import { useWorkspace } from "@/lib/workspace";
import { useAuth } from "@/lib/auth-context";
import type { CycleListEntry } from "@/lib/api";
import { pathLeaf, unitKey, UNIT_SEP } from "@/lib/ids";
import { campaignDisplayName, unitDisplayName } from "@/lib/names";
import { runPhaseLabel } from "@/lib/run-phase";
import { fmtPct0 } from "@/lib/format";

// Inline unit picker — the Dashboard masthead's title line. Single label
// scheme: optgroup = the campaign's display name, option
// `${unit} · best X · status`.
//
// A `cycle_id` is unique only within its campaign, so every option is keyed
// and valued by the composite `campaign_id::cycle_id` (unitKey); selecting
// one passes BOTH ids back.

function optionText(c: CycleListEntry): string {
  // run_phase while live (running/paused/detached); the precise terminal
  // reason (from `status`) once finished — one label, via the single helper.
  // `✎ babysat` flags a human-intervened (operator-skipped) cycle — a native
  // <option> can't host a badge, so it rides the label text.
  const babysat = c.human_intervened ? " · ✎ babysat" : "";
  return `${unitDisplayName(c)} · best ${fmtPct0(c.best_accuracy)} · ${runPhaseLabel(c.run_phase, c.status)}${babysat}`;
}

export const CyclePicker = memo(function CyclePicker() {
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
    viewedPath,
    backToOuter,
    campaigns,
  } = useWorkspace();
  const { status } = useAuth();

  // Group labels resolve through the same rename seam the sidebar uses, so a renamed
  // campaign reads the same in both. No hash: an id fragment is not a name.
  const campaignById = useMemo(
    () => new Map(campaigns.map((c) => [c.campaign_id, c])),
    [campaigns],
  );

  // Depth > 1 ⇒ viewing an inner descendant (an L4 inner loop). The picker's
  // select still binds to the ROOT hop (campaignId/cycleId); the breadcrumb
  // shows the leaf.
  const innerLeaf = viewedPath && viewedPath.length > 1 ? pathLeaf(viewedPath) : null;

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
    // Anon never loads the (auth-gated) workspace — show a terminal label, not a
    // perpetual "loading…" (frontend-surface-contract.md § I1).
    const restingLabel = status === "unauthed" ? "No campaign" : "loading…";
    return <span>{cycleId || restingLabel}</span>;
  }
  if (cycles.length === 0) {
    return <span>{cycleId || "No campaigns yet"}</span>;
  }

  const currentKey = campaignId && cycleId ? unitKey(campaignId, cycleId) : "";
  const selectedKnown = cycles.some(
    (c) => c.campaign_id === campaignId && c.cycle_id === cycleId,
  );

  return (
    <span className="cycle-picker">
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
          const campaign = campaignById.get(cid);
          const groupLabel = campaign
            ? campaignDisplayName(campaign)
            : entries[0]?.dataset_name || "(unknown)";
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
      {!following && !innerLeaf && (
        <button
          type="button"
          className="follow-active-btn"
          onClick={followActive}
          title="Pinned to this unit. Click to resume following the campaign the CLI is currently running."
        >
          ↪ Follow active
        </button>
      )}
      {innerLeaf && (
        <span className="inner-breadcrumb">
          <span className="inner-breadcrumb-sep" aria-hidden="true">
            ⤷
          </span>
          <span className="inner-breadcrumb-label" title={innerLeaf.campaignId}>
            inner: {innerLeaf.campaignId}
          </span>
          <button
            type="button"
            className="follow-active-btn"
            onClick={backToOuter}
            title="Viewing an inner loop's dashboard. Click to return to the outer cycle."
          >
            ↑ Back to outer
          </button>
        </span>
      )}
    </span>
  );
});
