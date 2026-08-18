"use client";
import type { ReactNode } from "react";
import { cx } from "@/lib/cx";
import { MORE_TABS, PRIMARY_TABS, isMoreTab, tabLabel, type Tab } from "@/lib/view-tab";

// The per-campaign view nav — the sidebar's own rows, since this app has no top
// bar at any width. Which view sits in which tier is `lib/view-tab.ts`.
//
// The 36px rail renders all four as icons with NO disclosure: the hierarchy
// rations horizontal TEXT, and hiding two there leaves the rail unable to switch
// views at all.

interface Props {
  tab: Tab;
  onSelect: (tab: Tab) => void;
  collapsed: boolean;
  // The operator's own preference. The RENDERED state is derived below — the group
  // also opens around an active view inside it, without writing this.
  moreOpen: boolean;
  onToggleMore: () => void;
}

const ICONS: Record<Tab, ReactNode> = {
  chat: (
    <path d="M2 4.5A1.5 1.5 0 0 1 3.5 3h9A1.5 1.5 0 0 1 14 4.5v5A1.5 1.5 0 0 1 12.5 11H6l-3 2.5V11H3.5A1.5 1.5 0 0 1 2 9.5z" />
  ),
  dashboard: <path d="M2.5 13V6.5M6.5 13V3M10.5 13V8M14 13H2" />,
  // Two bars side by side — the comparison, not another chart.
  compare: <path d="M4 13V7M8 13V3M12 13V9M2 13h12" />,
  verify: <path d="M2.5 8.5 6 12l7.5-8" />,
  files: (
    <path d="M2.5 4.5A1 1 0 0 1 3.5 3.5h2.2l1.3 1.6h5.5a1 1 0 0 1 1 1v6a1 1 0 0 1-1 1h-9a1 1 0 0 1-1-1z" />
  ),
};

function Glyph({ tab }: { tab: Tab }) {
  return (
    <svg
      width="16"
      height="16"
      viewBox="0 0 16 16"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.3"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      {ICONS[tab]}
    </svg>
  );
}

export function ViewNav({ tab, onSelect, collapsed, moreOpen, onToggleMore }: Props) {
  // An element factory, NOT a nested component — a component type minted per
  // render remounts these buttons on every poll and drops keyboard focus.
  const row = (id: Tab) => (
    <button
      key={id}
      type="button"
      className={cx("nav-item", tab === id && "active")}
      // A vertical nav is not a tab strip — `aria-current` is the list idiom, and
      // there is no `role="tabpanel"` on any pane for a tablist to point at.
      aria-current={tab === id ? "page" : undefined}
      title={collapsed ? tabLabel(id) : undefined}
      aria-label={collapsed ? tabLabel(id) : undefined}
      onClick={() => onSelect(id)}
    >
      <Glyph tab={id} />
      {!collapsed && <span>{tabLabel(id)}</span>}
    </button>
  );

  if (collapsed) {
    return (
      <nav className="sidebar-views collapsed" aria-label="Campaign view">
        {[...PRIMARY_TABS, ...MORE_TABS].map(row)}
      </nav>
    );
  }

  // Derived, never written: a view inside the group forces the group open, so the
  // active view can't be invisible — and returning to Chat restores the operator's
  // own preference rather than having silently changed it.
  const activeIsInside = isMoreTab(tab);
  const expanded = moreOpen || activeIsInside;

  return (
    <nav className="sidebar-views" aria-label="Campaign view">
      {PRIMARY_TABS.map(row)}
      <button
        type="button"
        className={cx("nav-item", "nav-item-more", activeIsInside && "disabled")}
        aria-expanded={expanded}
        // Honest rather than a no-op: while Verify or Files is the view in front
        // of the operator, collapsing the group that holds it would hide it.
        disabled={activeIsInside}
        title={activeIsInside ? `${tabLabel(tab)} is open` : undefined}
        onClick={onToggleMore}
      >
        <span className="nav-item-twist" aria-hidden="true">
          {expanded ? "‹" : "›"}
        </span>
        <span>{expanded ? "less" : "more"}</span>
      </button>
      {expanded &&
        MORE_TABS.map((id) => (
          <div key={id} className="sidebar-views-sub">
            {row(id)}
          </div>
        ))}
    </nav>
  );
}
