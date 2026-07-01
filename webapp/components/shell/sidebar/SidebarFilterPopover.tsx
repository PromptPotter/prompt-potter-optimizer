"use client";
import { useMemo, useState } from "react";
import { Popover } from "@/components/ui";
import { cx } from "@/lib/cx";
import type { LifecycleFilter } from "@/lib/api";

// The campaign-library filter, folded behind one button so the sidebar body
// stays a clean forest even with dozens of datasets. Opening it reveals the
// two controls that used to sit always-on above the tree — the lifecycle
// segment and the dataset picker — plus a type-to-filter box so picking a
// dataset out of a long list is direct instead of a scan down a chip wall.
// The trigger carries a dot whenever a non-default filter is set, so the
// operator can see the list is narrowed without opening the panel.

interface Props {
  lifecycleFilter: LifecycleFilter;
  setLifecycleFilter: (f: LifecycleFilter) => void;
  datasetNames: string[];
  datasetFilter: string | null;
  setDatasetFilter: (d: string | null) => void;
}

// Two-slider "adjust filters" glyph (currentColor → tints with theme + state).
const FILTER_GLYPH = (
  <svg
    viewBox="0 0 24 24"
    width="15"
    height="15"
    fill="none"
    stroke="currentColor"
    strokeWidth="1.7"
    strokeLinecap="round"
    aria-hidden="true"
  >
    <line x1="8" y1="4" x2="8" y2="20" />
    <line x1="16" y1="4" x2="16" y2="20" />
    <circle cx="8" cy="9" r="2.3" fill="var(--color-background-secondary)" />
    <circle cx="16" cy="15" r="2.3" fill="var(--color-background-secondary)" />
  </svg>
);

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
      <div
        className="unit-library-tabs"
        role="tablist"
        aria-label="Campaign lifecycle"
      >
        <button
          type="button"
          role="tab"
          className={cx("unit-library-tab", lifecycleFilter === "active" && "active")}
          onClick={() => setLifecycleFilter("active")}
          aria-selected={lifecycleFilter === "active"}
        >
          Active
        </button>
        <button
          type="button"
          role="tab"
          className={cx("unit-library-tab", lifecycleFilter === "archived" && "active")}
          onClick={() => setLifecycleFilter("archived")}
          aria-selected={lifecycleFilter === "archived"}
          title="Show archived campaigns. Deleted campaigns are hidden — read them by id from the file tree."
        >
          Archived
        </button>
      </div>

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
