"use client";
import { TERMS } from "@/lib/terms";
import { ThemeToggle } from "./ThemeToggle";
import type { Theme } from "@/lib/theme";

export type Tab = "newjob" | "results";

interface Props {
  tab: Tab;
  onTabChange: (t: Tab) => void;
  onThemeChange?: (t: Theme) => void;
}

export function Topbar({ tab, onTabChange, onThemeChange }: Props) {
  return (
    <header className="topbar">
      <input className="search" placeholder="Search analytics..." disabled aria-label="Search analytics" />
      <div className="tabs" role="tablist" aria-label="Workspace">
        <button
          type="button"
          className={`tab${tab === "results" ? " active" : ""}`}
          role="tab"
          tabIndex={0}
          aria-selected={tab === "results"}
          onClick={() => onTabChange("results")}
        >View Results</button>
        <button type="button" className="tab" role="tab" tabIndex={-1} aria-disabled="true" aria-selected="false" title={TERMS.placeholder}>Model Audit</button>
        <button type="button" className="tab" role="tab" tabIndex={-1} aria-disabled="true" aria-selected="false" title={TERMS.placeholder}>Prompts</button>
        <button type="button" className="tab" role="tab" tabIndex={-1} aria-disabled="true" aria-selected="false" title={TERMS.placeholder}>Datasets</button>
      </div>
      <button className="btn-export" disabled title={TERMS.placeholder}>Export Data</button>
      {/* New Job is demoted — used rarely (only when minting a new origin),
         so it lives as a small `+ New` next to the theme toggle rather than
         as a primary tab competing with View Results for attention. */}
      <button
        type="button"
        className={`btn-new-job${tab === "newjob" ? " active" : ""}`}
        onClick={() => onTabChange("newjob")}
        title="Start a new job"
        aria-pressed={tab === "newjob"}
      >+ New</button>
      <ThemeToggle onThemeChange={onThemeChange} />
    </header>
  );
}
