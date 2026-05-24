"use client";
import { applyTheme, readStoredTheme, type Theme } from "@/lib/theme";

// Per-cycle sub-tabs (Replit-style): the sidebar carries the campaign
// library; the topbar carries the views over the *currently-selected*
// campaign. Plus two install-wide tabs (Leverage, Verify) that aggregate
// across every campaign on disk and are not scoped to a single cycle.
export type Tab = "chat" | "dashboard" | "files" | "leverage" | "compare" | "verify";

interface Props {
  tab: Tab;
  onTabChange: (t: Tab) => void;
  onThemeChange?: (t: Theme) => void;
}

const TABS: { id: Tab; label: string }[] = [
  { id: "chat", label: "Chat" },
  { id: "dashboard", label: "Dashboard" },
  { id: "files", label: "Files" },
  { id: "compare", label: "Compare" },
  { id: "leverage", label: "Leverage" },
  { id: "verify", label: "Verify" },
];

export function Topbar({ tab, onTabChange, onThemeChange }: Props) {
  // Theme button has no React state — sun/moon icons are pure CSS off `data-theme`.
  const flipTheme = () => {
    const next: Theme = readStoredTheme() === "light" ? "dark" : "light";
    applyTheme(next);
    onThemeChange?.(next);
  };

  return (
    <header className="topbar">
      <div className="tabs" role="tablist" aria-label="Campaign view">
        {TABS.map((t) => (
          <button
            key={t.id}
            type="button"
            className={`tab${tab === t.id ? " active" : ""}`}
            role="tab"
            tabIndex={0}
            aria-selected={tab === t.id}
            onClick={() => onTabChange(t.id)}
          >{t.label}</button>
        ))}
      </div>
      <button
        className="theme-toggle"
        type="button"
        onClick={flipTheme}
        title="Toggle bright / dark theme"
        aria-label="Toggle theme"
      >
        <svg className="sun" width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" aria-hidden="true">
          <circle cx="8" cy="8" r="3" />
          <path d="M8 1v2M8 13v2M1 8h2M13 8h2M3.05 3.05l1.4 1.4M11.55 11.55l1.4 1.4M3.05 12.95l1.4-1.4M11.55 4.45l1.4-1.4" />
        </svg>
        <svg className="moon" width="16" height="16" viewBox="0 0 16 16" fill="currentColor" aria-hidden="true">
          <path d="M6 1.5A6.5 6.5 0 1 0 14.5 10 5 5 0 0 1 6 1.5z" />
        </svg>
      </button>
    </header>
  );
}
