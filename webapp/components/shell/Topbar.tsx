"use client";
import { TERMS } from "@/lib/terms";
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
      <div className="tabs" role="tablist" aria-label="Cycle view">
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
        <button type="button" className="tab" role="tab" tabIndex={-1} aria-disabled="true" aria-selected="false" title={TERMS.placeholder}>Model Audit</button>
        <button type="button" className="tab" role="tab" tabIndex={-1} aria-disabled="true" aria-selected="false" title={TERMS.placeholder}>Prompts</button>
      </div>
      <button className="btn-export" disabled title={TERMS.placeholder}>Export Data</button>
      <ThemeToggle onThemeChange={onThemeChange} />
    </header>
  );
}
