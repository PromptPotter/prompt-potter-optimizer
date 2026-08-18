"use client";
// Which campaigns the comparison pools. Reads the campaign list the workspace already fetched —
// no second registry read — and groups by dataset so a cross-dataset selection is a deliberate
// act rather than an accident.
//
// No backend_type filter and no L4 gate: an ordinary campaign and a self-optimizing one are the
// same kind of thing here.

import type { CampaignSummary } from "@/lib/api";

function byDataset(campaigns: readonly CampaignSummary[]): [string, CampaignSummary[]][] {
  const groups = new Map<string, CampaignSummary[]>();
  for (const c of campaigns) {
    const key = c.dataset_name || "(no dataset)";
    const bucket = groups.get(key);
    if (bucket) bucket.push(c);
    else groups.set(key, [c]);
  }
  for (const rows of groups.values()) {
    rows.sort((a, b) => b.created_at.localeCompare(a.created_at));
  }
  return [...groups.entries()].sort((a, b) => a[0].localeCompare(b[0]));
}

export function CampaignPicker({
  campaigns,
  selected,
  onToggle,
  onSelectDataset,
  onClear,
}: {
  campaigns: readonly CampaignSummary[];
  selected: readonly string[];
  onToggle: (campaignId: string) => void;
  onSelectDataset: (ids: readonly string[]) => void;
  onClear: () => void;
}) {
  const groups = byDataset(campaigns);
  const chosen = new Set(selected);
  return (
    <div className="cmp-picker">
      <div className="cmp-picker-head">
        <span className="l4-subtle">
          {selected.length} of {campaigns.length} selected
        </span>
        <button type="button" className="cmp-link" onClick={onClear} disabled={!selected.length}>
          Clear
        </button>
      </div>
      {groups.length === 0 ? (
        <p className="l4-empty">No campaigns yet.</p>
      ) : (
        groups.map(([dataset, rows]) => (
          <fieldset className="cmp-group" key={dataset}>
            <legend className="cmp-group-head">
              <span className="cmp-group-name">{dataset}</span>
              <button
                type="button"
                className="cmp-link"
                onClick={() => onSelectDataset(rows.map((r) => r.campaign_id))}
              >
                All {rows.length}
              </button>
            </legend>
            {rows.map((c) => (
              <label className="cmp-option" key={c.campaign_id}>
                <input
                  type="checkbox"
                  checked={chosen.has(c.campaign_id)}
                  onChange={() => onToggle(c.campaign_id)}
                />
                <span className="cmp-option-id">{c.campaign_id}</span>
                <span className="l4-dim">{c.created_at.slice(0, 16).replace("T", " ")}</span>
              </label>
            ))}
          </fieldset>
        ))
      )}
    </div>
  );
}
