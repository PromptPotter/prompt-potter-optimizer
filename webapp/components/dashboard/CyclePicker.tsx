"use client";
import { useWorkspace } from "@/lib/workspace";
import type { Campaign, CycleListEntry } from "@/lib/api";
import { rootCycleId, sessionIndexOf, campaignOriginHash, unitKey, UNIT_SEP } from "@/lib/ids";
import { campaignDisplayName, unitDisplayName } from "@/lib/names";
import { fmtPct0 } from "@/lib/format";

// Inline unit picker. The `breadcrumb` variant styles a native `<select>` to
// read as breadcrumb text (dashboard); the `standalone` variant prints the
// campaign + session name on its own (chat header). Either way the operator
// clicks it and gets a native dropdown grouped by campaign, then by session.
//
// A `cycle_id` is unique only within its campaign, so every option is keyed
// and valued by the composite `campaign_id::cycle_id` (unitKey); selecting
// one passes BOTH ids back.

// Breadcrumb-variant option label — unit name + its best score + status.
function breadcrumbLabel(c: CycleListEntry): string {
  return `${unitDisplayName(c)} · best ${fmtPct0(c.best_accuracy)} · ${c.status}`;
}

export function CyclePicker({
  variant = "breadcrumb",
}: {
  variant?: "breadcrumb" | "standalone";
}) {
  const {
    cycleId,
    campaignId,
    campaigns,
    cycles,
    cyclesLoaded,
    cyclesError,
    activeCycleId,
    activeCampaignId,
    selectCycle,
  } = useWorkspace();

  const standalone = variant === "standalone";

  if (cyclesError && cycles.length === 0) {
    return <span className="cycle-picker-err">campaigns: {cyclesError}</span>;
  }
  if (!cyclesLoaded) {
    return <span>{standalone ? "New Job" : cycleId || "loading…"}</span>;
  }
  if (cycles.length === 0) {
    return <span>{standalone ? "New Job" : cycleId || "no campaigns"}</span>;
  }

  // Campaign manifests by id — resolves the operator-facing campaign name
  // (label when set, else dataset name). The standalone variant prints it
  // into every option so the collapsed `<select>` trigger reads "Campaign ·
  // Session N" — a native trigger only ever shows the selected option text.
  const campaignById = new Map<string, Campaign>(
    campaigns.map((c) => [c.campaign_id, c]),
  );
  const campaignNameOf = (id: string, fallbackDataset: string): string => {
    const camp = campaignById.get(id);
    return camp ? campaignDisplayName(camp) : fallbackDataset || id;
  };

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
          const campName = campaignNameOf(cid, dataset);
          const groupLabel = standalone
            ? campName
            : `${dataset} · ${campaignOriginHash(cid).slice(0, 6)}`;
          return (
            <optgroup key={cid} label={groupLabel}>
              {entries.map((c) => {
                const k = unitKey(c.campaign_id, c.cycle_id);
                const isActive =
                  c.campaign_id === activeCampaignId &&
                  c.cycle_id === activeCycleId;
                const text = standalone
                  ? `${campName} · ${unitDisplayName(c)}`
                  : breadcrumbLabel(c);
                return (
                  <option key={k} value={k}>
                    {isActive ? "● " : ""}
                    {text}
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
