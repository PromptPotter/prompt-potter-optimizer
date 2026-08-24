"use client";
import { cx } from "@/lib/cx";
import { Popover, Switch } from "@/components/ui";
import { useRunControl } from "@/lib/hooks/useRunControl";

// The composer's "Tools" control — what this agent can do, one tap from the
// message box, in the slot every chat app puts it in. Deliberately quiet: a
// ghost chip between the input and Send, never an accent, because it is a
// drawer you open when you want it rather than a thing asking to be pressed.
//
// It is the ONLY home for these switches. They used to be a 360px Settings
// column in `.chat-grid`, which on a phone stacked underneath the composer —
// a card you had to scroll past the input to reach.
//
// Three are locked coming-soon (`Switch locked` renders the unavailability
// rather than a dead control styled like a live one, § I3). The fourth is
// real: optimizing while you use it IS the run, so its switch is the run's
// own pause/start verb (`useRunControl`), the same one the dashboard's
// play/pause button fires.

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
  // No cycle bound yet, or the run is alive but held somewhere this control does
  // not speak for (the origin gate, check-in). Either way the switch states WHY
  // instead of offering a press that misfires.
  const optimizeNote = !run
    ? "Starts once a campaign is running."
    : (run.noneReason ?? (run.pausing ? run.pausingNote : null));
  const optimizeLocked = !run || run.noneReason != null || run.pending;
  // ON wherever nothing has turned it OFF. Optimizing while you use it is what the
  // product IS, so before a run exists — no cycle bound, at the origin gate, still
  // warming — the switch shows the default rather than an OFF that reads as "this
  // app is not doing the one thing it does". Once the control speaks for a real run
  // it tells the truth: a paused cycle reads OFF, because someone paused it.
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
          // The label survives the phone, where the word is hidden for width.
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
