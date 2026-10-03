"use client";
import { useMemo, useState } from "react";
import { Icon, Popover, SegmentedControl, type Segment } from "@/components/ui";
import { cx } from "@/lib/cx";
import type { LifecycleFilter } from "@/lib/api";

// The campaign-library filter. The trigger carries a dot whenever a non-default filter is set —
// a narrowed list must never look like a complete one.

interface Props {
  lifecycleFilter: LifecycleFilter;
  setLifecycleFilter: (f: LifecycleFilter) => void;
  datasetNames: string[];
  datasetFilter: string | null;
  setDatasetFilter: (d: string | null) => void;
}

const FILTER_GLYPH = (
  <Icon size={15} strokeWidth={1.7}>
    <line x1="8" y1="4" x2="8" y2="20" />
    <line x1="16" y1="4" x2="16" y2="20" />
    <circle cx="8" cy="9" r="2.3" fill="var(--color-background-secondary)" />
    <circle cx="16" cy="15" r="2.3" fill="var(--color-background-secondary)" />
  </Icon>
);

const LIFECYCLE_SEGMENTS: readonly Segment<LifecycleFilter>[] = [
  { value: "active", label: "Active" },
  {
    value: "archived",
    label: "Archived",
    title:
      "Show archived campaigns. Deleted campaigns are hidden — read them by id from the file tree.",
  },
];

export function SidebarFilterPopover({
  lifecycleFilter,
  setLifecycleFilter,
  datasetNames,
  datasetFilter,
  setDatasetFilter,
}: Props) {
  const active = lifecycleFilter === "archived" || datasetFilter != null;

  return (
    <Popover
      align="right"
      renderTrigger={({ open, toggle }) => (
        <button
          type="button"
          className={cx("unit-library-filter-btn", (open || active) && "on")}
          onClick={toggle}
          aria-expanded={open}
          aria-haspopup="dialog"
          aria-label="Filter campaigns"
          title="Filter campaigns"
        >
          {FILTER_GLYPH}
          {active && <span className="unit-library-filter-dot" aria-hidden="true" />}
        </button>
      )}
    >
      {() => (
        <FilterPanel
          lifecycleFilter={lifecycleFilter}
          setLifecycleFilter={setLifecycleFilter}
          datasetNames={datasetNames}
          datasetFilter={datasetFilter}
          setDatasetFilter={setDatasetFilter}
        />
      )}
    </Popover>
  );
}

function FilterPanel({
  lifecycleFilter,
  setLifecycleFilter,
  datasetNames,
  datasetFilter,
  setDatasetFilter,
}: Props) {
  const [q, setQ] = useState("");
  const shown = useMemo(() => {
    const needle = q.trim().toLowerCase();
    return needle
      ? datasetNames.filter((d) => d.toLowerCase().includes(needle))
      : datasetNames;
  }, [datasetNames, q]);

  return (
    <div className="sidebar-filter-panel" role="dialog" aria-label="Filter campaigns">
      <SegmentedControl
        className="sidebar-filter-lifecycle"
        options={LIFECYCLE_SEGMENTS}
        value={lifecycleFilter}
        onChange={setLifecycleFilter}
        ariaLabel="Campaign lifecycle"
      />

      {datasetNames.length > 1 && (
        <>
          <input
            type="search"
            className="sidebar-filter-search"
            placeholder="Filter datasets…"
            value={q}
            onChange={(e) => setQ(e.target.value)}
            aria-label="Filter datasets by name"
          />
          <div
            className="unit-library-filter sidebar-filter-datasets"
            role="group"
            aria-label="Filter by dataset"
          >
            <button
              type="button"
              className={cx("unit-library-filter-chip", datasetFilter == null && "active")}
              onClick={() => setDatasetFilter(null)}
            >
              All datasets
            </button>
            {shown.map((d) => (
              <button
                key={d}
                type="button"
                className={cx("unit-library-filter-chip", datasetFilter === d && "active")}
                onClick={() => setDatasetFilter(datasetFilter === d ? null : d)}
                title={d}
              >
                {d}
              </button>
            ))}
            {shown.length === 0 && (
              <span className="sidebar-filter-empty">No dataset matches “{q}”.</span>
            )}
          </div>
        </>
      )}
    </div>
  );
}
