import { Popover, Switch } from "@/components/ui";

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

const TOOLS_ICON = (
  <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" aria-hidden="true">
    <path d="M2 4.5h5M11 4.5h3M2 11.5h3M9 11.5h5" />
    <circle cx="9" cy="4.5" r="1.8" />
    <circle cx="7" cy="11.5" r="1.8" />
  </svg>
);

export function ComposerTools() {
  return (
    <Popover
      align="right"
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
          <ToolRow icon={THINK_ICON} name="Extended thinking" />
          <ToolRow icon={SEARCH_ICON} name="Web search" />
          <ToolRow icon={CODE_ICON} name="Code execution" />
        </div>
      )}
    </Popover>
  );
}

// Every row is a tool still to come: shown, locked off.
function ToolRow({ icon, name }: { icon: React.ReactNode; name: string }) {
  return (
    <div className="toggle-row">
      <div className="row-text">
        <span className="row-icon">{icon}</span>
        <div className="row-body">
          <div className="name">
            {name}
            <span className="soon-tag">Soon</span>
          </div>
        </div>
      </div>
      <Switch checked={false} label={name} locked />
    </div>
  );
}
