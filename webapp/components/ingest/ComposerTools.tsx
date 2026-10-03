"use client";
import { cx } from "@/lib/cx";
import { Popover, Switch } from "@/components/ui";
import { useRunControl } from "@/lib/hooks/useRunControl";

// The composer's "Tools" drawer — the only home for these switches. The optimize switch
// IS the run's pause/start verb, the same one the dashboard's play/pause fires.

const THINK_ICON = (
  <svg width="16" height="16" viewBox="0 0 16 16" fill="currentColor" aria-hidden="true">
    <circle cx="8" cy="8" r="6" opacity=".3" />
    <path d="M8 4v4l3 2" stroke="currentColor" strokeWidth="1.5" fill="none" strokeLinecap="round" />
  </svg>
);

const SEARCH_ICON = (
  <svg width="16" height="16" viewBox="0 0 16 16" fill="currentColor" aria-hidden="true">
    <circle cx="8" cy="8" r="6" stroke="currentColor" strokeWidth="1.2" fill="none" />
    <path d="M2 8h12M8 2c2 1.8 2 10.2 0 12M8 2c-2 1.8-2 10.2 0 12" stroke="currentColor" strokeWidth="1.1" fill="none" />
  </svg>
);

const CODE_ICON = (
  <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    <path d="M5 4 1.5 8 5 12" />
    <path d="M11 4l3.5 4L11 12" />
    <path d="M9.5 3.5l-3 9" opacity=".6" />
  </svg>
);

const WAND_ICON = (
  <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    <path d="M2.5 13.5 10 6" />
    <path d="m12 1.5.7 2 2 .7-2 .7-.7 2-.7-2-2-.7 2-.7Z" fill="currentColor" />
    <path d="m5 2.4.4 1.1 1.1.4-1.1.4L5 5.4l-.4-1.1-1.1-.4 1.1-.4Z" fill="currentColor" opacity=".7" />
  </svg>
);

const TOOLS_ICON = (
  <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" aria-hidden="true">
    <path d="M2 4.5h5M11 4.5h3M2 11.5h3M9 11.5h5" />
    <circle cx="9" cy="4.5" r="1.8" />
    <circle cx="7" cy="11.5" r="1.8" />
  </svg>
);

export function ComposerTools() {
  const run = useRunControl();
  const optimizeNote = !run
    ? "Starts once a campaign is running."
    : (run.noneReason ?? (run.pausing ? run.pausingNote : null));
  const optimizeLocked = !run || run.noneReason != null || run.pending;
  // ON until a real run says otherwise: before one exists the default shows, never an OFF.
  const optimizeOn = !run || run.noneReason != null ? true : run.running;

  return (
    <Popover
      align="right"
      side="top"
      className="chat-tools-wrap"
      renderTrigger={({ open, toggle }) => (
        <button
          type="button"
          className="chat-tools"
          aria-expanded={open}
          aria-haspopup="true"
          aria-label="Tools"
          title="Tools this chat can use"
          onClick={toggle}
        >
          {TOOLS_ICON}
          <span>Tools</span>
        </button>
      )}
    >
      {() => (
        <div className="chat-tools-panel">
          <ToolRow icon={THINK_ICON} name="Extended thinking" soon />
          <ToolRow icon={SEARCH_ICON} name="Web search" soon />
          <ToolRow icon={CODE_ICON} name="Code execution" soon />
          <div className="row-separator" />
          <ToolRow
            icon={WAND_ICON}
            wand
            name="Optimize prompt while using"
            desc={optimizeNote ?? "Quietly evolves parameters across your project"}
            checked={optimizeOn}
            onChange={run?.toggle}
            locked={optimizeLocked}
            lockedNote={optimizeNote ?? "unavailable"}
          />
          {run?.err ? (
            <p className="chat-tools-err" role="alert">
              {run.err}
            </p>
          ) : null}
        </div>
      )}
    </Popover>
  );
}

function ToolRow({
  icon,
  name,
  desc,
  soon,
  wand,
  checked = false,
  onChange,
  locked,
  lockedNote,
}: {
  icon: React.ReactNode;
  name: string;
  desc?: string;
  soon?: boolean;
  wand?: boolean;
  checked?: boolean;
  onChange?: () => void;
  locked?: boolean;
  lockedNote?: string;
}) {
  return (
    <div className={cx("toggle-row", wand && "wand-row")}>
      <div className="row-text">
        <span className="row-icon">{icon}</span>
        <div className="row-body">
          <div className="name">
            {name}
            {soon ? <span className="soon-tag">Soon</span> : null}
          </div>
          {desc ? <div className="desc">{desc}</div> : null}
        </div>
      </div>
      <Switch
        checked={checked}
        onChange={onChange}
        label={name}
        locked={soon || locked}
        lockedNote={soon ? undefined : lockedNote}
      />
    </div>
  );
}
