"use client";
import { useWorkspace } from "@/lib/workspace";
import { Popover } from "@/components/ui";

// The most direct "is anything running?" signal, sitting in the topbar next to
// the view tabs. Absent when idle, the Potter glyph when one cycle is live, and
// the glyph + a count badge when several are. It reads the same `cycles` list
// the sidebar renders (polled by WorkspaceProvider) and filters on the
// live `running` flag — no separate poll, no separate source of truth.

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
  const { cycles, campaigns, selectCycle } = useWorkspace();
  const running = cycles.filter((c) => c.run_phase === "running");
  const n = running.length;

  // Missing button === idle: the absence IS the signal.
  if (n === 0) return null;

  const pick = (campaignId: string, cycleId: string) => {
    selectCycle(campaignId, cycleId);
    onPicked?.();
  };

  const labelFor = (campaignId: string, dataset: string) =>
    campaigns.find((c) => c.campaign_id === campaignId)?.label || dataset || campaignId;

  // Exactly one running → the button IS the direct link.
  if (n === 1) {
    const c = running[0];
    return (
      <button
        type="button"
        className="topbar-jobs"
        aria-label={`1 campaign running — ${labelFor(c.campaign_id, c.dataset_name)}. Go to it.`}
        title={`Running: ${labelFor(c.campaign_id, c.dataset_name)}`}
        onClick={() => pick(c.campaign_id, c.cycle_id)}
      >
        {POTTER_GLYPH}
      </button>
    );
  }

  // Several running → glyph + count badge; click opens the list.
  return (
    <Popover
      align="left"
      renderTrigger={({ open, toggle }) => (
        <button
          type="button"
          className="topbar-jobs"
          aria-label={`${n} campaigns running. Open the list.`}
          aria-expanded={open}
          aria-haspopup="menu"
          title={`${n} campaigns running`}
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
        <ul className="topbar-jobs-list" role="menu" aria-label="Running campaigns">
          {running.map((c) => (
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
                <span className="topbar-jobs-dot" aria-hidden="true" />
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
