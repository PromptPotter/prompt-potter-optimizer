"use client";
import { useWorkspace } from "@/lib/workspace";
import type { CycleListEntry } from "@/lib/api";
import { rootCycleId, shortFamilyTail, sessionIndexOf, campaignOriginHash } from "@/lib/ids";

// Inline unit picker for the dashboard breadcrumb. The breadcrumb text
// becomes a `<select>` styled to read as text — the operator clicks it and
// gets a native dropdown grouped by campaign, then by session.
//
// A `cycle_id` is unique only within its campaign, so every option is keyed
// and valued by the composite `campaign_id::cycle_id`; selecting one passes
// BOTH ids back.

const SEP = "::";

function unitKey(campaignId: string, cycleId: string): string {
  return `${campaignId}${SEP}${cycleId}`;
}

// Operator-facing unit-kind labels — the time-horizon taxonomy.
const UNIT_KIND_LABEL: Record<string, string> = {
  session: "session",
  divergent_resume: "divergent resume",
  user_fork: "user fork",
  l3_fork: "L3 fork",
};

export function CyclePicker() {
  const {
    cycleId,
    campaignId,
    cycles,
    cyclesLoaded,
    cyclesError,
    activeCycleId,
    activeCampaignId,
    selectCycle,
  } = useWorkspace();

  if (cyclesError && cycles.length === 0) {
    return <span className="cycle-picker-err">campaigns: {cyclesError}</span>;
  }
  if (!cyclesLoaded) {
    return <span>{cycleId || "loading…"}</span>;
  }
  if (cycles.length === 0) {
    return <span>{cycleId || "no campaigns"}</span>;
  }

  // Group options by campaign. Within a campaign, order by session index;
  // each session root is followed by its forks (root first, then by recency).
  const groups = new Map<string, CycleListEntry[]>();
  for (const c of cycles) {
    const arr = groups.get(c.campaign_id) ?? [];
    arr.push(c);
    groups.set(c.campaign_id, arr);
  }
  for (const arr of groups.values()) {
    arr.sort((a, b) => {
      const sa = sessionIndexOf(rootCycleId(a.cycle_id));
      const sb = sessionIndexOf(rootCycleId(b.cycle_id));
      if (sa !== sb) return sa - sb;
      if (a.is_root !== b.is_root) return a.is_root ? -1 : 1;
      return a.updated_at < b.updated_at ? 1 : -1;
    });
  }
  // Optgroups ordered most-recently-active campaign first.
  const lastTouched = (entries: CycleListEntry[]) =>
    Math.max(...entries.map((c) => Date.parse(c.updated_at) || 0));
  const groupKeys = [...groups.keys()].sort(
    (a, b) => lastTouched(groups.get(b)!) - lastTouched(groups.get(a)!),
  );

  const currentKey = campaignId && cycleId ? unitKey(campaignId, cycleId) : "";
  const selectedKnown = cycles.some(
    (c) => c.campaign_id === campaignId && c.cycle_id === cycleId,
  );

  return (
    <span className="cycle-picker">
      <select
        value={currentKey}
        onChange={(e) => {
          const idx = e.target.value.indexOf(SEP);
          if (idx < 0) return;
          selectCycle(
            e.target.value.slice(0, idx),
            e.target.value.slice(idx + SEP.length),
          );
        }}
        aria-label="Switch unit"
      >
        {!selectedKnown && currentKey && (
          <option value={currentKey} disabled>
            {cycleId} (not on disk)
          </option>
        )}
        {groupKeys.map((cid) => {
          const entries = groups.get(cid)!;
          const dataset = entries[0]?.dataset_name || "(unknown)";
          return (
            <optgroup
              key={cid}
              label={`${dataset} · ${campaignOriginHash(cid).slice(0, 6)}`}
            >
              {entries.map((c) => {
                const k = unitKey(c.campaign_id, c.cycle_id);
                const isActive =
                  c.campaign_id === activeCampaignId &&
                  c.cycle_id === activeCycleId;
                return (
                  <option key={k} value={k}>
                    {isActive ? "● " : ""}
                    {labelFor(c)}
                  </option>
                );
              })}
            </optgroup>
          );
        })}
      </select>
    </span>
  );
}

function labelFor(c: CycleListEntry): string {
  const best = c.best_accuracy == null ? "—" : `${(c.best_accuracy * 100).toFixed(0)}%`;
  // A session root reads as "session N"; a branch reads as its kind + the
  // short id tail (shortFamilyTail already prefixes a kind glyph).
  const name = c.is_root
    ? `session ${sessionIndexOf(c.cycle_id)}`
    : `${UNIT_KIND_LABEL[c.unit_kind] ?? c.unit_kind} ${shortFamilyTail(c.cycle_id)}`;
  return `${name} · best ${best} · ${c.status}`;
}
