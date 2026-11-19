import { useMemo, useState } from "react";
import { Icon, Popover, SegmentedControl, type Segment } from "@/components/ui";
import { cx } from "@/lib/cx";
import { isFiltered, type ListedLifecycle, setListedFilter, useListedFilter } from "./listed-filter";

const FILTER_GLYPH = (
  <Icon size={15} strokeWidth={1.7}>
    <line x1="8" y1="4" x2="8" y2="20" />
    <line x1="16" y1="4" x2="16" y2="20" />
    <circle cx="8" cy="9" r="2.3" fill="var(--color-background-secondary)" />
    <circle cx="16" cy="15" r="2.3" fill="var(--color-background-secondary)" />
  </Icon>
);

const LIFECYCLE_SEGMENTS: readonly Segment<ListedLifecycle>[] = [
  { value: "active", label: "Active" },
  {
    value: "archived",
    label: "Archived",
    hint:
      "Show archived campaigns. Deleted campaigns are hidden — read them by id from the file tree.",
  },
];

export function SidebarFilterPopover({ datasetNames }: { datasetNames: string[] }) {
  const active = isFiltered(useListedFilter());

  return (
    <Popover
      align="right"
      renderTrigger={({ open, toggle }) => (
        <button
          type="button"
          className={cx("unit-library-filter-btn", (open || active) && "on")}
          onClick={toggle}
          aria-expanded={open}
          aria-label="Filter campaigns"
          title="Filter campaigns"
        >
          {FILTER_GLYPH}
          {active && <span className="unit-library-filter-dot" aria-hidden="true" />}
        </button>
      )}
    >
      {() => <FilterPanel datasetNames={datasetNames} />}
    </Popover>
  );
}

function FilterPanel({ datasetNames }: { datasetNames: string[] }) {
  const { lifecycle, dataset } = useListedFilter();
  const [q, setQ] = useState("");
  const shown = useMemo(() => {
    const needle = q.trim().toLowerCase();
    return needle
      ? datasetNames.filter((d) => d.toLowerCase().includes(needle))
      : datasetNames;
  }, [datasetNames, q]);

  return (
    <div className="sidebar-filter-panel" role="group" aria-label="Filter campaigns">
      <SegmentedControl
        className="sidebar-filter-lifecycle"
        options={LIFECYCLE_SEGMENTS}
        value={lifecycle}
        onChange={(next) => setListedFilter({ lifecycle: next })}
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
              className={cx("unit-library-filter-chip", dataset == null && "active")}
              onClick={() => setListedFilter({ dataset: null })}
            >
              All datasets
            </button>
            {shown.map((d) => (
              <button
                key={d}
                type="button"
                className={cx("unit-library-filter-chip", dataset === d && "active")}
                onClick={() => setListedFilter({ dataset: dataset === d ? null : d })}
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
