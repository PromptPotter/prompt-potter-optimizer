"use client";
import { useWorkspace } from "@/lib/workspace";
import { runPhaseLabel } from "@/lib/run-phase";
import { campaignDisplayName } from "@/lib/names";
import { cx } from "@/lib/cx";
import { Menu, MenuItem } from "@/components/ui";
import { PotterMark } from "@/components/brand/PotterMark";

// The live-run dock on the sidebar's outer edge; absent when idle — the absence IS "all quiet",
// which is why it lists PRODUCERS only (never `paused` or `detached`).

interface Props {
  onPicked?: () => void;
}

const POTTER_GLYPH = <PotterMark size={16} />;

export function JobsDock({ onPicked }: Props) {
  const { runningCycles, campaigns, selectCycle } = useWorkspace();
  const n = runningCycles.length;

  if (n === 0) return null;

  const pick = (campaignId: string, cycleId: string) => {
    selectCycle(campaignId, cycleId);
    onPicked?.();
  };

  const labelFor = (campaignId: string) => {
    const campaign = campaigns.find((c) => c.campaign_id === campaignId);
    return campaign ? campaignDisplayName(campaign) : campaignId;
  };

  // Still carries its phase class: a run held at the origin gate must not look like one
  // making progress.
  const c = n === 1 ? runningCycles[0] : undefined;
  if (c) {
    const label = labelFor(c.campaign_id);
    return (
      <div className="jobs-dock">
        <button
          type="button"
          className={cx("jobs-dock-btn", `phase-${c.run_phase}`)}
          aria-label={`1 active job — ${label} (${runPhaseLabel(c.run_phase, c.status)}). Go to it.`}
          title={`${runPhaseLabel(c.run_phase, c.status)}: ${label}`}
          onClick={() => pick(c.campaign_id, c.cycle_id)}
        >
          {POTTER_GLYPH}
        </button>
      </div>
    );
  }

  return (
    <div className="jobs-dock">
      <Menu
        align="left"
        renderTrigger={({ open, toggle }) => (
          <button
            type="button"
            className="jobs-dock-btn"
            aria-label={`${n} active jobs. Open the list.`}
            aria-expanded={open}
            aria-haspopup="menu"
            title={`${n} active jobs`}
            onClick={toggle}
          >
            {POTTER_GLYPH}
            <span className="jobs-dock-count" aria-hidden="true">
              {n}
            </span>
          </button>
        )}
      >
        {({ close }) => (
          <>
            {/* Already in the shared `dockPriority` order — never re-sorted here. */}
            {runningCycles.map((c) => (
              <MenuItem
                key={`${c.campaign_id}/${c.cycle_id}`}
                onClick={() => {
                  pick(c.campaign_id, c.cycle_id);
                  close();
                }}
              >
                <span className="jobs-dock-row">
                  <span className={cx("phase-chip", `phase-${c.run_phase}`)}>
                    <span className="phase-dot" aria-hidden="true" />
                    {runPhaseLabel(c.run_phase, c.status)}
                  </span>
                  <span className="jobs-dock-item-label">{labelFor(c.campaign_id)}</span>
                </span>
              </MenuItem>
            ))}
          </>
        )}
      </Menu>
    </div>
  );
}
