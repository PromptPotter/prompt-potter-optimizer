"use client";
import { type HardSamplesScope } from "@/lib/api";

interface Props {
  // Data scope toggle — campaign-pooled vs cross-campaign archive. Rendered
  // only when the owner wires both `scope` and `onScopeChange`.
  scope?: HardSamplesScope;
  onScopeChange?: (s: HardSamplesScope) => void;
  // Auto-sort toggle state — the table mirrors the optimizer's live
  // difficulty sort when on.
  syncLive: boolean;
  onToggleSyncLive: () => void;
  // Hide-unmeasured toggle state — rows without any measurements are
  // filtered out when on.
  hideUnmeasured: boolean;
  onToggleHideUnmeasured: () => void;
  datasetName: string | null;
  measuredCount: number;
  unmeasuredCount: number;
  // Held-out test fold size from campaign.json — a footnote; those samples
  // are not rows in this table.
  datasetSplitTest: number | null;
  onResetLayout: () => void;
}

// Footer strip beneath the roster grid: data-scope toggle, the Auto-sort
// checkbox, the measured/unmeasured counts, and the layout reset.
export function HardSamplesFooter({
  scope,
  onScopeChange,
  syncLive,
  onToggleSyncLive,
  hideUnmeasured,
  onToggleHideUnmeasured,
  datasetName,
  measuredCount,
  unmeasuredCount,
  datasetSplitTest,
  onResetLayout,
}: Props) {
  const total = measuredCount + unmeasuredCount;
  const tag = datasetName ? `${datasetName} · ` : "";

  return (
    <div className="hs-footer">
      {scope && onScopeChange ? (
        <div
          className="hs-scope-toggle"
          role="radiogroup"
          aria-label="Hard-sample data scope"
          title={
            scope === "campaign"
              ? "Showing this campaign's pooled evidence. Toggle to every campaign on this dataset."
              : "Showing every campaign on this dataset (cross-campaign archive). Toggle to this campaign only."
          }
        >
          <button
            type="button"
            role="radio"
            aria-checked={scope === "campaign"}
            className={`hs-scope-opt${scope === "campaign" ? " on" : ""}`}
            onClick={() => onScopeChange("campaign")}
          >
            This campaign
          </button>
          <button
            type="button"
            role="radio"
            aria-checked={scope === "dataset"}
            className={`hs-scope-opt${scope === "dataset" ? " on" : ""}`}
            onClick={() => onScopeChange("dataset")}
          >
            All campaigns (dataset)
          </button>
        </div>
      ) : null}
      <label
        className="hs-sync-toggle"
        title={
          syncLive
            ? "Sort follows the picker's Info-gain ranking. Order only refreshes when the artifact regenerates (round boundary) — not on every 2 s poll, so the table doesn't flash. Untick to sort columns manually."
            : "Sort with column headers. Tick to follow the picker's Info-gain ranking — order refreshes once per round, not per poll."
        }
      >
        <input type="checkbox" checked={syncLive} onChange={onToggleSyncLive} />
        Auto-sort
      </label>
      <label
        className="hs-sync-toggle"
        title={
          hideUnmeasured
            ? "Showing only samples with at least one measurement. Untick to show every sample."
            : "Showing every sample, including those the optimizer hasn't measured yet. Tick to hide unmeasured rows."
        }
      >
        <input
          type="checkbox"
          checked={hideUnmeasured}
          onChange={onToggleHideUnmeasured}
        />
        Hide unmeasured
      </label>
      <span
        className="hs-counts"
        title={
          datasetSplitTest != null
            ? `This table is the ${total}-sample training bank. A separate ` +
              `${datasetSplitTest}-sample test fold is held out — not shown here.`
            : undefined
        }
      >
        {tag}Measured {measuredCount} · Unmeasured {unmeasuredCount} · Total{" "}
        {total}
        {datasetSplitTest != null ? ` · ${datasetSplitTest} test held out` : ""}
      </span>
      <button
        type="button"
        className="hs-reset"
        onClick={onResetLayout}
        title="Reset column widths, folds, wraps, sort"
      >
        Reset layout
      </button>
    </div>
  );
}
