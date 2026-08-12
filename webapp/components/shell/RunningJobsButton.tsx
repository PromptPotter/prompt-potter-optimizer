"use client";
import { useWorkspace } from "@/lib/workspace";
import { runPhaseLabel } from "@/lib/run-phase";
import { cx } from "@/lib/cx";
import { Popover } from "@/components/ui";
import { PotterMark } from "@/components/brand/PotterMark";

// The OS-style dock of open units, sitting in the topbar next to the view tabs:
// aggregated, collapsed, glanceable. Absent when idle (the absence IS the "all
// quiet" signal), the Potter glyph when one unit is live, glyph + a count badge
// when several are. Clicking an entry OPENS it (jumps the view to its dashboard).
// It reads the workspace's shared `liveCycles` derivation (running / gate /
// paused) — the same in-flight set the RemoteBar uses, so they can't disagree.
// `detached` (dead producer) is not an open app and never appears here; running
// units sort above suspended (paused) ones so "what's executing" reads first.

interface Props {
  // Called after a running cycle is picked, so the topbar can switch to the
  // Dashboard view — clicking a live run should land you on its dashboard.
  onPicked?: () => void;
}

// currentColor → inherits the button's text colour, so the glyph tints with
// theme + hover. Shared definition in components/brand/PotterMark.
const POTTER_GLYPH = <PotterMark size={16} />;

export function RunningJobsButton({ onPicked }: Props) {
  const { liveCycles, campaigns, selectCycle } = useWorkspace();
  const n = liveCycles.length;

  // Missing button === idle: the absence IS the signal.
  if (n === 0) return null;

  const pick = (campaignId: string, cycleId: string) => {
    selectCycle(campaignId, cycleId);
    onPicked?.();
  };

  const labelFor = (campaignId: string, dataset: string) =>
    campaigns.find((c) => c.campaign_id === campaignId)?.label || dataset || campaignId;

  // Exactly one in flight → the button IS the direct link. It still carries its
  // phase class: one live unit is the COMMON case, and dropping the phase here
  // meant a run held at the origin gate — blocked on the operator, making no
  // progress — was pixel-identical to one working fine. The phase now reads at a
  // glance in both branches, not only in the multi-unit popover.
  const c = n === 1 ? liveCycles[0] : undefined;
  if (c) {
    const label = labelFor(c.campaign_id, c.dataset_name);
    return (
      <button
        type="button"
        className={cx("topbar-jobs", `phase-${c.run_phase}`)}
        aria-label={`1 active job — ${label} (${runPhaseLabel(c.run_phase, c.status)}). Go to it.`}
        title={`${runPhaseLabel(c.run_phase, c.status)}: ${label}`}
        onClick={() => pick(c.campaign_id, c.cycle_id)}
      >
        {POTTER_GLYPH}
      </button>
    );
  }

  // Several in flight → glyph + count badge; click opens the list, each row
  // tagged with its phase so running / paused / detached read at a glance.
  return (
    <Popover
      align="left"
      renderTrigger={({ open, toggle }) => (
        <button
          type="button"
          className="topbar-jobs"
          aria-label={`${n} active jobs. Open the list.`}
          aria-expanded={open}
          aria-haspopup="menu"
          title={`${n} active jobs`}
          onClick={toggle}
        >
          {POTTER_GLYPH}
          <span className="topbar-jobs-count" aria-hidden="true">
            {n}
          </span>
        </button>
      )}
    >
      {({ close }) => (
        <ul className="topbar-jobs-list" role="menu" aria-label="Active jobs">
          {/* liveCycles already carries the shared order (running above paused) —
              see workspace.tsx's liveCycles memo. */}
          {liveCycles.map((c) => (
            <li key={`${c.campaign_id}/${c.cycle_id}`} role="none">
              <button
                type="button"
                role="menuitem"
                className="topbar-jobs-item"
                onClick={() => {
                  pick(c.campaign_id, c.cycle_id);
                  close();
                }}
              >
                <span className={cx("phase-chip", `phase-${c.run_phase}`)}>
                  <span className="phase-dot" aria-hidden="true" />
                  {runPhaseLabel(c.run_phase, c.status)}
                </span>
                <span className="topbar-jobs-item-label">
                  {labelFor(c.campaign_id, c.dataset_name)}
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </Popover>
  );
}
