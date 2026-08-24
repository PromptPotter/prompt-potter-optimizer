"use client";
import { CyclePicker } from "@/components/shell/CyclePicker";
import { ViewTabs } from "@/components/shell/ViewTabs";
import { useWorkspace } from "@/lib/workspace";
import type { Tab } from "@/lib/view-tab";

// The unit's masthead — the picker styled as the title line, the leaf cycle id
// beneath in mono, the view strip under both. ONE header over every tab (chrome),
// so no two can grow different answers to "what am I looking at". It sits in
// `main` rather than inside a pane's scroller: the strip is the only way to switch
// views, and one that scrolls out of reach on a long dashboard is no nav at all.
// The band is full-bleed; the inner box centres on --dash-narrow-max, the one
// method the chat column and the dashboard spine also use (cycle-picker.css).
export function RunMasthead({ tab, onSelectTab }: { tab: Tab; onSelectTab: (t: Tab) => void }) {
  const { leafCycleId } = useWorkspace();
  return (
    <header className="run-header">
      <div className="run-header-inner">
        <div className="run-title">
          <CyclePicker />
        </div>
        {leafCycleId ? <span className="run-id">ID: {leafCycleId}</span> : null}
        <ViewTabs tab={tab} onSelect={onSelectTab} />
      </div>
    </header>
  );
}
