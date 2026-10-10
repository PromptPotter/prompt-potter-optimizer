"use client";
import type { ReactNode } from "react";
import { SegmentedControl, type Segment } from "@/components/ui";
import { useWorkspace } from "@/lib/workspace";
import { preloadLazyPanes } from "./lazy-panes";
import {
  PRIMARY_TABS,
  RECORDS_ENTRY,
  RECORDS_LABEL,
  RECORDS_TABS,
  groupOf,
  isRecordsTab,
  tabLabel,
  type CampaignTab,
  type RecordsTab,
  type Tab,
  type ViewGroup,
} from "@/lib/view-tab";

const ICONS: Record<Tab, ReactNode> = {
  chat: (
    <path d="M2 4.5A1.5 1.5 0 0 1 3.5 3h9A1.5 1.5 0 0 1 14 4.5v5A1.5 1.5 0 0 1 12.5 11H6l-3 2.5V11H3.5A1.5 1.5 0 0 1 2 9.5z" />
  ),
  dashboard: <path d="M2.5 13V6.5M6.5 13V3M10.5 13V8M14 13H2" />,
  measurements: <path d="M2.5 3.5h11M2.5 6.5h11M2.5 9.5h11M2.5 12.5h7" />,
  compare: <path d="M4 13V7M8 13V3M12 13V9M2 13h12" />,
  files: (
    <path d="M2.5 4.5A1 1 0 0 1 3.5 3.5h2.2l1.3 1.6h5.5a1 1 0 0 1 1 1v6a1 1 0 0 1-1 1h-9a1 1 0 0 1-1-1z" />
  ),
};

export function ViewGlyph({ tab }: { tab: Tab }) {
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

const PRIMARY_SEGMENTS: readonly Segment<ViewGroup>[] = [
  ...PRIMARY_TABS.map((t) => ({
    value: t,
    label: (
      <>
        <ViewGlyph tab={t} />
        {tabLabel(t)}
      </>
    ),
  })),
  {
    value: "records",
    label: (
      <>
        <ViewGlyph tab="files" />
        {RECORDS_LABEL}
      </>
    ),
  },
];

const RECORDS_SEGMENTS: readonly Segment<RecordsTab>[] = RECORDS_TABS.map((t) => ({
  value: t,
  label: tabLabel(t),
}));

export function ViewTabs({ tab }: { tab: CampaignTab }) {
  const { openView: onSelect } = useWorkspace();
  const onIntent = preloadLazyPanes;
  // The group segment fires even when already on: a re-click on Files must not bounce to the entry.
  const pickGroup = (group: ViewGroup) => {
    if (group !== "records") onSelect(group);
    else if (!isRecordsTab(tab)) onSelect(RECORDS_ENTRY);
  };

  return (
    <nav
      className="view-tabs"
      aria-label="Campaign view"
      onPointerEnter={onIntent}
      onFocus={onIntent}
    >
      <SegmentedControl
        size="lg"
        options={PRIMARY_SEGMENTS}
        value={groupOf(tab)}
        onChange={pickGroup}
        ariaLabel="Campaign view"
      />
    </nav>
  );
}

export function RecordsTabs({ tab }: { tab: RecordsTab }) {
  const { openView: onSelect } = useWorkspace();
  return (
    <div className="view-subnav">
      <SegmentedControl
        options={RECORDS_SEGMENTS}
        value={tab}
        onChange={onSelect}
        ariaLabel={RECORDS_LABEL}
      />
    </div>
  );
}
