"use client";
import type { ReactNode } from "react";
import { cx } from "@/lib/cx";
import { SegmentedControl, type Segment } from "@/components/ui";
import {
  PRIMARY_TABS,
  RECORDS_ENTRY,
  RECORDS_LABEL,
  RECORDS_TABS,
  groupOf,
  isRecordsTab,
  tabLabel,
  type RecordsTab,
  type Tab,
  type ViewGroup,
} from "@/lib/view-tab";

// The per-campaign view axis, on the page under the run title — the app's ONE nav
// surface for it, at every width. Two rows: the top level (Chat · Dashboard ·
// Records), and the Records members, which appear only while one of them is the
// view. Which view sits in which tier is `lib/view-tab.ts`.

// `Record<Tab, …>` on purpose: adding a view is a compile error here until it has a
// glyph, which is the one place the type system enforces nav completeness.
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

const PRIMARY_SEGMENTS: readonly Segment<ViewGroup>[] = [
  ...PRIMARY_TABS.map((t) => ({
    value: t,
    label: (
      <>
        <Glyph tab={t} />
        {tabLabel(t)}
      </>
    ),
  })),
  {
    value: "records",
    label: (
      <>
        <Glyph tab="files" />
        {RECORDS_LABEL}
      </>
    ),
  },
];

const RECORDS_SEGMENTS: readonly Segment<RecordsTab>[] = RECORDS_TABS.map((t) => ({
  value: t,
  label: tabLabel(t),
}));

export function ViewTabs({
  tab,
  onSelect,
  className,
}: {
  tab: Tab;
  onSelect: (tab: Tab) => void;
  className?: string;
}) {
  // The group segment fires on click even when it is already on, so entering
  // Records is guarded: re-clicking it while reading Files must not bounce back
  // to the entry member.
  const pickGroup = (group: ViewGroup) => {
    if (group !== "records") onSelect(group);
    else if (!isRecordsTab(tab)) onSelect(RECORDS_ENTRY);
  };

  return (
    <div className={cx("view-tabs", className)}>
      <SegmentedControl
        size="lg"
        options={PRIMARY_SEGMENTS}
        value={groupOf(tab)}
        onChange={pickGroup}
        ariaLabel="Campaign view"
      />
      {isRecordsTab(tab) && (
        <SegmentedControl
          options={RECORDS_SEGMENTS}
          value={tab}
          onChange={onSelect}
          ariaLabel={RECORDS_LABEL}
        />
      )}
    </div>
  );
}
