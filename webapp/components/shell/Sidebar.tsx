"use client";
import { TERMS } from "@/lib/terms";

export type Pane = "dashboard" | "files";

interface Props {
  pane: Pane;
  onSelect: (p: Pane) => void;
}

interface NavSpec {
  pane: Pane | null; // null = placeholder
  label: string;
  icon: React.ReactNode;
}

const NAV: NavSpec[] = [
  {
    pane: "dashboard",
    label: "Dashboard",
    icon: (
      <svg width="16" height="16" viewBox="0 0 16 16" fill="currentColor" style={{ opacity: 0.6 }} aria-hidden="true">
        <rect x="1" y="1" width="6" height="6" rx="1" />
        <rect x="9" y="1" width="6" height="6" rx="1" opacity=".5" />
        <rect x="1" y="9" width="6" height="6" rx="1" opacity=".5" />
        <rect x="9" y="9" width="6" height="6" rx="1" opacity=".5" />
      </svg>
    ),
  },
  {
    pane: "files",
    label: "Files",
    icon: (
      <svg width="16" height="16" viewBox="0 0 16 16" fill="currentColor" style={{ opacity: 0.6 }} aria-hidden="true">
        <path d="M2 3a1 1 0 0 1 1-1h3.5l1.5 1.5H13a1 1 0 0 1 1 1V13a1 1 0 0 1-1 1H3a1 1 0 0 1-1-1V3z" opacity=".4" />
        <rect x="2" y="5" width="12" height="9" rx="1" />
      </svg>
    ),
  },
  {
    pane: null,
    label: "Analytics",
    icon: (
      <svg width="16" height="16" viewBox="0 0 16 16" fill="currentColor" style={{ opacity: 0.6 }} aria-hidden="true">
        <rect x="2" y="8" width="2" height="6" rx="1" />
        <rect x="5" y="5" width="2" height="9" rx="1" opacity=".7" />
        <rect x="8" y="3" width="2" height="11" rx="1" opacity=".5" />
        <rect x="11" y="6" width="2" height="8" rx="1" opacity=".4" />
      </svg>
    ),
  },
  {
    pane: null,
    label: "Evaluations",
    icon: (
      <svg width="16" height="16" viewBox="0 0 16 16" fill="currentColor" style={{ opacity: 0.6 }} aria-hidden="true">
        <circle cx="8" cy="8" r="6" opacity=".3" />
        <path d="M8 4v4l3 2" stroke="currentColor" strokeWidth="1.5" fill="none" strokeLinecap="round" />
      </svg>
    ),
  },
  {
    pane: null,
    label: "Reports",
    icon: (
      <svg width="16" height="16" viewBox="0 0 16 16" fill="currentColor" style={{ opacity: 0.6 }} aria-hidden="true">
        <rect x="2" y="2" width="12" height="12" rx="1" opacity=".2" />
        <rect x="4" y="5" width="8" height="1.5" rx=".75" />
        <rect x="4" y="8" width="6" height="1.5" rx=".75" />
        <rect x="4" y="11" width="4" height="1.5" rx=".75" />
      </svg>
    ),
  },
  {
    pane: null,
    label: "Settings",
    icon: (
      <svg width="16" height="16" viewBox="0 0 16 16" fill="currentColor" style={{ opacity: 0.6 }} aria-hidden="true">
        <circle cx="8" cy="8" r="2.5" />
        <path d="M8 1v2M8 13v2M1 8h2M13 8h2" stroke="currentColor" strokeWidth="1.2" fill="none" strokeLinecap="round" />
      </svg>
    ),
  },
];

export function Sidebar({ pane, onSelect }: Props) {
  return (
    <nav className="sidebar" aria-label="Primary">
      <div className="brand">
        <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 2 }}>
          <div style={{ width: 22, height: 22, background: "var(--color-accent)", borderRadius: 4, display: "flex", alignItems: "center", justifyContent: "center" }}>
            <svg width="12" height="12" viewBox="0 0 12 12" fill="none" aria-hidden="true">
              <rect x="1" y="1" width="4" height="4" rx="1" fill="white" />
              <rect x="7" y="1" width="4" height="4" rx="1" fill="white" opacity=".7" />
              <rect x="1" y="7" width="4" height="4" rx="1" fill="white" opacity=".7" />
              <rect x="7" y="7" width="4" height="4" rx="1" fill="white" opacity=".4" />
            </svg>
          </div>
          <span className="brand-name">PromptPotter</span>
        </div>
        <div className="brand-sub" title={TERMS.brand_live_preview}>LIVE PREVIEW</div>
      </div>
      {NAV.map((n) => {
        if (n.pane == null) {
          return (
            <button
              key={n.label}
              type="button"
              className="nav-item disabled"
              tabIndex={-1}
              aria-disabled="true"
              title={TERMS.placeholder}
            >
              {n.icon}
              {n.label}
            </button>
          );
        }
        const active = pane === n.pane;
        return (
          <button
            key={n.label}
            type="button"
            className={`nav-item${active ? " active" : ""}`}
            onClick={() => n.pane && onSelect(n.pane)}
            aria-current={active ? "page" : undefined}
          >
            {n.icon}
            {n.label}
          </button>
        );
      })}
      <div style={{ padding: "12px 16px", marginTop: 12 }}>
        <button className="new-analysis" disabled title={TERMS.placeholder}>+ New Analysis</button>
      </div>
      <div className="sidebar-footer">
        <div className="sidebar-footer-item">Support</div>
        <div className="sidebar-footer-item">Log out</div>
      </div>
    </nav>
  );
}
