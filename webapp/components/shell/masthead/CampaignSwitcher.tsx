"use client";
import { Fragment, useMemo } from "react";
import { Menu, MenuItem } from "@/components/ui";
import { useAuth } from "@/lib/auth-context";
import type { OriginGroup, RunGroup } from "@/lib/derivations";
import { unitDisplayName } from "@/lib/names";
import { useRegistry } from "@/lib/registry";
import { useWorkspace } from "@/lib/workspace";
import { CampaignRowLabel, PhaseMark } from "../sidebar/CampaignRowLabel";

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

export function CampaignSwitcher() {
  const { campaignId, cycleId, navigate } = useWorkspace();
  const { forest, cycles, cyclesLoaded, failure } = useRegistry();
  const { status } = useAuth();
  const groups = useMemo(() => byDataset(forest), [forest]);

  if (failure && cycles.length === 0) {
    return <span className="run-switch-err">campaigns: {failure.message}</span>;
  }
  if (!cyclesLoaded) {
    // Anon never loads the workspace: a terminal label, not a perpetual "loading…".
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
                      navigate([
                        {
                          campaignId: run.campaign.campaign_id,
                          cycleId: run.campaign.root_cycle_id,
                        },
                      ]);
                      close();
                    }}
                  >
                    <span className="run-switch-row">
                      <CampaignRowLabel run={run} />
                    </span>
                  </MenuItem>
                  {run.branches.map((branch) => (
                    <MenuItem
                      key={branch.cycle_id}
                      onClick={() => {
                        navigate([
                          { campaignId: branch.campaign_id, cycleId: branch.cycle_id },
                        ]);
                        close();
                      }}
                    >
                      <span className="run-switch-branch">
                        <PhaseMark status={branch.status} />
                        {unitDisplayName(branch)}
                      </span>
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
