"use client";
import { Fragment, useMemo } from "react";
import { FailureLine, Menu, MenuItem } from "@/components/ui";
import { useAuth } from "@/lib/state/auth";
import type { ListedCampaign } from "@/lib/api";
import { tagValue } from "@/lib/derivations";
import { decodeCyclePath, encodeCyclePath, type CyclePath } from "@/lib/ids";
import { useIngest } from "@/lib/ingest-flow";
import { useRegistry } from "@/lib/state/registry";
import { useWorkspace } from "@/lib/workspace";
import { CampaignRowLabel, PhaseMark } from "../sidebar/CampaignRowLabel";

function byDataset(entries: readonly ListedCampaign[]): [string, ListedCampaign[]][] {
  const out = new Map<string, ListedCampaign[]>();
  for (const campaign of entries) {
    const dataset = tagValue(campaign, "dataset") ?? "";
    const arr = out.get(dataset) ?? [];
    arr.push(campaign);
    out.set(dataset, arr);
  }
  return [...out];
}

export function CampaignSwitcher() {
  const { campaignId, cycleId, navigate } = useWorkspace();
  const { listed, byAddress, loaded, failure } = useRegistry();
  const { status } = useAuth();
  const { openDraft } = useIngest();
  const groups = useMemo(() => byDataset(listed), [listed]);
  const go = (address: string, close: () => void) => {
    const path: CyclePath | null = decodeCyclePath(address);
    if (path) navigate(path);
    close();
  };

  if (failure && listed.length === 0) {
    return <FailureLine failure={failure} lead="campaigns:" />;
  }
  if (!loaded) {
    // Anon never loads the workspace: a terminal label, not a perpetual "loading…".
    return (
      <span className="run-switch-note">{status === "unauthed" ? "No campaign" : "loading…"}</span>
    );
  }
  if (listed.length === 0) return <span className="run-switch-note">No campaigns yet</span>;

  const selectedKnown =
    !!campaignId && !!cycleId && byAddress.has(encodeCyclePath([{ campaignId, cycleId }]));

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
          {groups.map(([dataset, campaigns]) => (
            <div key={dataset} role="group" aria-label={dataset} className="run-switch-group">
              <span className="run-switch-dataset">{dataset}</span>
              {campaigns.map((campaign) => (
                <Fragment key={campaign.address}>
                  <MenuItem
                    onClick={() => {
                      if (campaign.level === "campaign") return go(campaign.facts.root, close);
                      // Unstarted: its check-in reopens, as its sidebar row does.
                      openDraft(campaign.address);
                      close();
                    }}
                  >
                    <span className="run-switch-row">
                      <CampaignRowLabel campaign={campaign} />
                    </span>
                  </MenuItem>
                  {campaign.level === "campaign" &&
                    campaign.children
                      .filter((branch) => branch.address !== campaign.facts.root)
                      .map((branch) => (
                        <MenuItem key={branch.address} onClick={() => go(branch.address, close)}>
                          <span className="run-switch-branch">
                            <PhaseMark status={branch.state.status} />
                            {branch.label}
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
