"use client";
import { type HardSampleOrder, type HardSamplesScope } from "@/lib/api";
import { SegmentedControl } from "@/components/ui";
import { ORDER_COLUMN } from "./columns";

interface Props {
  // Data scope toggle — campaign-pooled vs cross-campaign archive. Rendered
  // only when the owner wires both `scope` and `onScopeChange`.
  scope?: HardSamplesScope;
  onScopeChange?: (s: HardSamplesScope) => void;
  // Auto-sort toggle state — the table mirrors the optimizer's live
  // difficulty sort when on.
  syncLive: boolean;
  onToggleSyncLive: () => void;
  // The key the server ranked by, off its own echo; `null` while the read is in
  // flight. Named here rather than assumed, because these tooltips are what told
  // the operator a δ-ranked roster was ranked by Info gain.
  rankedBy: HardSampleOrder | null;
  rankedByPick: HardSampleOrder | null;
  onRankedByChange: (o: HardSampleOrder) => void;
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
  rankedBy,
  rankedByPick,
  onRankedByChange,
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
  // Falls back to a bare "the served ranking" while unknown — true either way, and it
  // never names a key the roster might not be in.
  const orderLabel = rankedBy ? `${ORDER_COLUMN[rankedBy].label} ` : "";

  return (
    <div className="hs-footer">
      {scope && onScopeChange ? (
        <SegmentedControl
          ariaLabel="Hard-sample data scope"
          value={scope}
          onChange={onScopeChange}
          options={[
            {
              value: "campaign",
              label: "This campaign",
              title:
                "Showing this campaign's pooled evidence. Switch to every campaign on this dataset.",
            },
            {
              value: "dataset",
              label: "All campaigns (dataset)",
              title:
                "Showing every campaign on this dataset (cross-campaign archive). Switch to this campaign only.",
            },
          ]}
        />
      ) : null}
      {/* The ranking key. Rendered only once the server has said which one the rows are
          actually in — an exclusive control with no true value would have to invent one,
          and inventing this one is the defect this pane just shed. */}
      {rankedBy ? (
        <SegmentedControl
          ariaLabel="Hard-sample ranking key"
          value={rankedByPick ?? rankedBy}
          onChange={onRankedByChange}
          options={[
            {
              value: "info_gain",
              label: "Info gain",
              title:
                "Rank by expected decision-information gain — which measurement would tell the optimizer the most.",
            },
            {
              value: "difficulty",
              label: "Hardness",
              title: "Rank by fitted difficulty (δ), hardest first.",
            },
          ]}
        />
      ) : null}
      <label
        className="hs-sync-toggle"
        title={
          syncLive
            ? `Sort follows the served ${orderLabel}ranking. Order only refreshes when the artifact regenerates (round boundary) — not on every 2 s poll, so the table doesn't flash. Untick to sort columns manually.`
            : `Sort with column headers. Tick to follow the served ${orderLabel}ranking — order refreshes once per round, not per poll.`
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
