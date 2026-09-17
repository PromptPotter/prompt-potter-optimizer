"use client";
import { useWorkspace } from "@/lib/workspace";
import { runPhaseLabel } from "@/lib/run-phase";
import { campaignDisplayName } from "@/lib/names";
import { cx } from "@/lib/cx";
import { Menu, MenuItem } from "@/components/ui";
import { PotterMark } from "@/components/brand/PotterMark";

// The dock of live runs, floating on the SIDEBAR'S OUTER EDGE and straddling the
// hairline: a run's existence is not a per-campaign fact, so it sits beside the
// library rather than inside it. Absent when idle — the absence IS "all quiet".
//
// It reads the workspace's shared `runningCycles` (running / gate, in the shared
// `dockPriority` order so what needs a decision reads first). PRODUCERS only:
// `paused` is a run the operator parked, so listing it kept the dock permanently lit
// and destroyed the all-quiet signal — it stays reachable as a sidebar row wearing
// its phase. `detached` is a dead producer, and never appears either.
//
// A direct child of `.shell`, NOT of `.sidebar`: the dock straddles the sidebar's edge,
// and `.sidebar{overflow:hidden}` would shave its outer half. Desktop only — a phone shows
// the same signal as the dot on the app bar's back arrow, off the same `runningCycles`.

interface Props {
  // Called after a running cycle is picked, so the shell can switch to the
  // Dashboard view — clicking a live run should land you on its dashboard.
  onPicked?: () => void;
}

const POTTER_GLYPH = <PotterMark size={16} />;

export function JobsDock({ onPicked }: Props) {
  const { runningCycles, campaigns, selectCycle } = useWorkspace();
  const n = runningCycles.length;

  // Missing dock === idle: the absence IS the signal.
  if (n === 0) return null;

  const pick = (campaignId: string, cycleId: string) => {
    selectCycle(campaignId, cycleId);
    onPicked?.();
  };

  // A cycle the campaign list does not carry (archived, filtered) still names the run it is:
  // its campaign id is the identity, and the dataset name alone would not say which run.
  const labelFor = (campaignId: string) => {
    const campaign = campaigns.find((c) => c.campaign_id === campaignId);
    return campaign ? campaignDisplayName(campaign) : campaignId;
  };

  // Exactly one in flight → the button IS the direct link, and still carries its
  // phase class: a run held at the origin gate is blocked on the operator and must
  // not be pixel-identical to one making progress.
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
            {/* runningCycles already carries the shared order (dockPriority) —
                see workspace.tsx's runningCycles memo. */}
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
