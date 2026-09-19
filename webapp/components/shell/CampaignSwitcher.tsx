"use client";
import { Fragment, useMemo } from "react";
import { Menu, MenuItem } from "@/components/ui";
import { useAuth } from "@/lib/auth-context";
import {
  campaignLineParts,
  campaignStatus,
  campaignTitle,
  spendLabel,
  type OriginGroup,
  type RunGroup,
} from "@/lib/derivations";
import { campaignDisplayName, unitDisplayName } from "@/lib/names";
import { useWorkspace } from "@/lib/workspace";
import { CampaignRowLabel } from "./sidebar/CampaignRowLabel";

// The masthead's campaign switcher — the forest the sidebar draws, one click from the title.
// It renders `CampaignRowLabel`, so a campaign cannot read one way here and another there;
// picking a campaign selects its ROOT cycle, and a branch under it selects that branch.

// Dataset headings over the forest's own order. A HEADING, not a second grouping: the runs
// inside keep the order `buildForest` gave them, and nothing here re-sorts or re-roots.
function byDataset(origins: OriginGroup[]): [string, RunGroup[]][] {
  const out = new Map<string, RunGroup[]>();
  for (const origin of origins) {
    for (const run of origin.runs) {
      const arr = out.get(run.campaign.dataset_name) ?? [];
      arr.push(run);
      out.set(run.campaign.dataset_name, arr);
    }
  }
  return [...out];
}

export function CampaignSwitcher({ origins }: { origins: OriginGroup[] }) {
  const { campaignId, cycleId, cycles, cyclesLoaded, cyclesError, selectCycle } = useWorkspace();
  const { status } = useAuth();
  const groups = useMemo(() => byDataset(origins), [origins]);

  if (cyclesError && cycles.length === 0) {
    return <span className="run-switch-err">campaigns: {cyclesError}</span>;
  }
  if (!cyclesLoaded) {
    // Anon never loads the (auth-gated) workspace — a terminal label, not a perpetual
    // "loading…" (frontend-surface-contract.md § I1).
    return (
      <span className="run-switch-note">{status === "unauthed" ? "No campaign" : "loading…"}</span>
    );
  }
  if (cycles.length === 0) return <span className="run-switch-note">No campaigns yet</span>;

  const selectedKnown = cycles.some(
    (c) => c.campaign_id === campaignId && c.cycle_id === cycleId,
  );

  return (
    <Menu
      renderTrigger={({ open, toggle }) => (
        <button
          type="button"
          className="run-switch"
          aria-label="Switch campaign"
          aria-expanded={open}
          onClick={toggle}
        >
          ▾
        </button>
      )}
    >
      {({ close }) => (
        <>
          {!selectedKnown && cycleId && (
            <MenuItem onClick={close} disabled>
              {cycleId} (not on disk)
            </MenuItem>
          )}
          {groups.map(([dataset, runs]) => (
            <div key={dataset} role="group" aria-label={dataset} className="run-switch-group">
              <span className="run-switch-dataset">{dataset}</span>
              {runs.map((run) => (
                <Fragment key={run.campaign.campaign_id}>
                  <MenuItem
                    onClick={() => {
                      selectCycle(run.campaign.campaign_id, run.root.cycle_id);
                      close();
                    }}
                  >
                    <span className="run-switch-row">
                      <CampaignRowLabel
                        name={campaignDisplayName(run.campaign)}
                        suffix={campaignTitle(run.campaign).suffix}
                        status={campaignStatus(run)}
                        spend={spendLabel(run.campaign)}
                        parts={campaignLineParts(run)}
                      />
                    </span>
                  </MenuItem>
                  {run.branches.map((branch) => (
                    <MenuItem
                      key={branch.cycle_id}
                      onClick={() => {
                        selectCycle(branch.campaign_id, branch.cycle_id);
                        close();
                      }}
                    >
                      <span className="run-switch-branch">{unitDisplayName(branch)}</span>
                    </MenuItem>
                  ))}
                </Fragment>
              ))}
            </div>
          ))}
        </>
      )}
    </Menu>
  );
}
