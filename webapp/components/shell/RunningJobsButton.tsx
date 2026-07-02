"use client";
import { useWorkspace } from "@/lib/workspace";
import { runPhaseLabel } from "@/lib/run-phase";
import { cx } from "@/lib/cx";
import { Popover } from "@/components/ui";

// The most direct "is anything running?" signal, sitting in the topbar next to
// the view tabs. Absent when idle, the Potter glyph when one job is in flight,
// and the glyph + a count badge when several are. It reads the workspace's
// shared `liveCycles` derivation (running / gate / paused / detached) — the same
// in-flight set the RemoteBar uses — so it can't disagree, and it no longer
// silently vanishes when a live cycle goes detached (e.g. an L4 inner loop).

interface Props {
  // Called after a running cycle is picked, so the topbar can switch to the
  // Dashboard view — clicking a live run should land you on its dashboard.
  onPicked?: () => void;
}

// Inline copy of public/brand/potter-mark.svg (currentColor → inherits the
// button's text colour). Kept inline so the glyph tints with theme + hover.
const POTTER_GLYPH = (
  <svg viewBox="0 0 24 24" width="16" height="16" fill="none" aria-hidden="true">
    <path
      d="M12 1.6 21.5 7v10L12 22.4 2.5 17V7z"
      stroke="currentColor"
      strokeWidth="1.4"
      strokeLinejoin="round"
    />
    <path d="M12 6.8 8 14.8h8z" fill="currentColor" />
    <path d="M8 16.9h8" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
    <path d="M3 12h3.4M17.6 12H21" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
  </svg>
);

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

  // Exactly one in flight → the button IS the direct link.
  if (n === 1) {
    const c = liveCycles[0];
    const label = labelFor(c.campaign_id, c.dataset_name);
    return (
      <button
        type="button"
        className="topbar-jobs"
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
