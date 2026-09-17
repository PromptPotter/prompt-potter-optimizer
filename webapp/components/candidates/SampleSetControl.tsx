"use client";
// The "fixed sample set" utility for the per-candidate fitness chart, lifted out
// of the candidates card so it's a self-contained, reusable unit (a future monitoring /
// export surface can mount it as-is). It reads/writes the shared
// `SelectionContext.sampleSet` axis directly — no prop-drilling — and owns only
// its local detail-drill state.
//
// Layout: the per-sample chip strip is the MAIN INFO (every campaign sample,
// highlighted = in the set the OVERLAP bars are read on); everything below it is control of
// that strip — clear/fill, per-round aggregate picks, and an opt-in sample-trajectory drill.
// It moves that one series and nothing else: the metric bars stay on each candidate's own
// cells whatever is picked here.

import { useState } from "react";
import type { MeasuredUnit, OverlapReading, RoundSummary } from "@/lib/api/types";
import { unitCount } from "@/lib/format";
import { cx } from "@/lib/cx";
import { useSelection } from "@/lib/SelectionContext";
import {
  measuredUniverse,
  roundMeasuredSets,
  roundsCoveringSample,
  sameSampleSet,
  toggleInSet,
} from "@/lib/sample-set";
import { Button, Chip, ChipGroup, HoverCard, SegmentedControl, type Segment } from "@/components/ui";
import { SampleTrajectorySeries } from "@/components/dashboard/samples/SampleTrajectory";
import { subsetExactFor, useScoringMask } from "@/components/shell/mask/scoring-mask";

// What a square in the trajectory grid below is allowed to stand for. Exclusive, so it is a
// segmented control rather than two toggles that can both be off.
type LoadMode = "measured" | "planned";

const LOAD_MODES: readonly Segment<LoadMode>[] = [
  { value: "measured", label: "measured only" },
  { value: "planned", label: "+ planned" },
];

export function SampleSetControl({
  rounds,
  overlap,
  unit,
}: {
  rounds: RoundSummary[];
  overlap: OverlapReading | null;
  unit: MeasuredUnit;
}) {
  const { sampleSet, setSelectionForSampleSet } = useSelection();
  const { open: maskOpen, mask } = useScoringMask();
  const [detailOpen, setDetailOpen] = useState(false);
  const [load, setLoad] = useState<LoadMode>("measured");
  // The one bar a picked set still moves besides the overlap ones: the server composes `lens`
  // and `samples` in the same read, so a criterion that cannot re-derive whole from the masked
  // rows comes back on a basis the bars beside it are not on, and is dropped.
  const maskDropped = maskOpen && !subsetExactFor(mask);

  if (sampleSet == null) return null; // mode off — nothing to control

  const universe = measuredUniverse(rounds);
  const roundSets = roundMeasuredSets(rounds);
  const inSet = new Set(sampleSet);
  // Which cells are the fixed yardstick, and how widely each cell was measured. A chip every
  // round bought can carry a cross-round comparison; one a single round bought cannot, and
  // before this they looked the same, so a bar computed over seven cells read like a result.
  const coverage = roundsCoveringSample(rounds);
  const fullyCovered = roundSets.length;
  // The engine's own shared set — the cells EVERY member of the adopted line answered. A
  // stronger guarantee than the coverage count beside it: that one says how many ROUNDS bought a
  // cell, this says the adopted line has all of it, which is what makes a cross-round difference
  // legitimate. Served, and it is what the chart's overlap bars sit on until something here
  // replaces it.
  const shared = new Set(overlap?.sample_ids ?? []);

  return (
    <div className="ss-control">
      {/* MAIN INFO — every campaign sample; highlighted = in the set the bars
          are computed over. Click any to toggle. */}
      <div className="ss-strip">
        {universe.map((sid) => {
          const on = inSet.has(sid);
          const seen = coverage.get(sid) ?? 0;
          const everywhere = seen >= fullyCovered && fullyCovered > 0;
          const note =
            `Measured in ${seen}/${fullyCovered} rounds` +
            (everywhere
              ? " — every bar can be read on it."
              : " — a bar for a round that never bought it is blank, not zero.") +
            (shared.has(sid) ? " On the served set: C0 and every winner since answered it." : "");
          return (
            // Three independent facts, three channels, so none hides another: SELECTED is the
            // fill, COVERAGE the opacity, and a cell on the SERVED set is underlined in the
            // overlap ink — more than a `Chip` can carry. `title` rather than a `HoverCard`
            // because the strip is one control per sample, and the `aria-label` beside it is
            // what says the state to a reader who cannot see the fill.
            <button
              key={sid}
              type="button"
              className={cx("ss-cell", on && "on", everywhere && "everywhere", shared.has(sid) && "shared")}
              aria-pressed={on}
              aria-label={`Sample ${sid} — ${on ? "in" : "not in"} the overlap set. ${note}`}
              title={`Sample #${sid} — ${on ? "in" : "not in"} the overlap set. Click to toggle. ${note}`}
              onClick={() => setSelectionForSampleSet(toggleInSet(sampleSet, sid))}
            >
              {sid}
            </button>
          );
        })}
      </div>

      {/* Controls for the strip above. */}
      <div className="ss-row">
        <Button className="ss-action" onClick={() => setSelectionForSampleSet(universe)}>
          All measured
        </Button>
        <HoverCard content="Deselect every sample and build the set up one at a time. Press ∩ above to close the picker.">
          <Button className="ss-action" onClick={() => setSelectionForSampleSet([])}>
            Off
          </Button>
        </HoverCard>
        {overlap != null && (
          <HoverCard
            content={`The ${unitCount(overlap.sample_ids.length, unit)} C0 and every winner since have all answered — the one basis they can be differenced on, and what the overlap bars sit on until you replace it.`}
          >
            <Chip
              on={sameSampleSet(sampleSet, overlap.sample_ids)}
              onClick={() => setSelectionForSampleSet(overlap.sample_ids)}
            >
              overlap · {overlap.sample_ids.length}
            </Chip>
          </HoverCard>
        )}
        <ChipGroup label="round" showLabel>
          {roundSets.map((rs) => (
            <Chip
              key={rs.round}
              on={sameSampleSet(sampleSet, rs.ids)}
              ariaLabel={`Round ${rs.round} — ${unitCount(rs.ids.length, unit)}`}
              title={`${unitCount(rs.ids.length, unit)} measured in round ${rs.round}`}
              onClick={() => setSelectionForSampleSet(rs.ids)}
            >
              R{rs.round}
            </Chip>
          ))}
        </ChipGroup>
        <span className="ss-count">
          {sampleSet.length}/{universe.length}
          {maskDropped ? " · mask off" : ""}
        </span>
      </div>

      {/* Detail drill — quiet text link; the spacious sample-trajectory grid stays collapsed
          and out of the way until asked for. */}
      <button
        type="button"
        className="ss-detail-toggle"
        aria-expanded={detailOpen}
        onClick={() => setDetailOpen((v) => !v)}
      >
        {detailOpen ? "hide detail" : "pick a state in detail…"}
      </button>
      {detailOpen && (
        <div className="ss-detail">
          <div className="ss-row">
            <span className="ss-label">a square loads:</span>
            <SegmentedControl
              options={LOAD_MODES}
              value={load}
              onChange={setLoad}
              ariaLabel="What a square in the grid below stands for"
            />
          </div>
          <SampleTrajectorySeries
            rounds={rounds}
            selectMode={load === "planned" ? "all" : "measured"}
            maxHeight={200}
          />
        </div>
      )}
    </div>
  );
}
