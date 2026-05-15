"use client";
import { ThemeToggle } from "./ThemeToggle";
import type { Theme } from "@/lib/theme";

// Per-cycle sub-tabs (Replit-style): the sidebar carries the cycle
// library; the topbar carries the views over the *currently-selected*
// cycle. Three views: Chat (conversational interface), Dashboard
// (live metrics + lineage + inspector), Files (filesystem browser).
export type Tab = "chat" | "dashboard" | "files";

interface Props {
  tab: Tab;
  onTabChange: (t: Tab) => void;
  onThemeChange?: (t: Theme) => void;
}

const TABS: { id: Tab; label: string }[] = [
  { id: "chat", label: "Chat" },
  { id: "dashboard", label: "Dashboard" },
  { id: "files", label: "Files" },
];

export function Topbar({ tab, onTabChange, onThemeChange }: Props) {
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
      <ThemeToggle onThemeChange={onThemeChange} />
    </header>
  );
}
