"use client";
import { ThemeToggle } from "./ThemeToggle";

// Per-cycle sub-tabs (Replit-style): the sidebar carries the campaign
// library; the topbar carries the views over the *currently-selected*
// campaign. `files` is part of the type but not rendered here — the
// StatusBar's "Open files" link is the sole entry point into FilesPane.
export type Tab = "chat" | "dashboard" | "files" | "verify";

interface Props {
  tab: Tab;
  onTabChange: (t: Tab) => void;
}

const TABS: { id: Tab; label: string }[] = [
  { id: "chat", label: "Chat" },
  { id: "dashboard", label: "Dashboard" },
  { id: "verify", label: "Verify" },
];

export function Topbar({ tab, onTabChange }: Props) {
  return (
    <header className="topbar">
      <input className="search" placeholder="Search analytics..." disabled aria-label="Search analytics" />
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
      <ThemeToggle />
    </header>
  );
}
