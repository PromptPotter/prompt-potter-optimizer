"use client";
// Picks `SelectionContext.sampleSet`, the cells the OVERLAP bars are read on. It moves that one
// series only: the metric bars stay on each candidate's own cells.

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
  // The server composes `lens` and `samples` in one read, so a criterion that cannot re-derive
  // whole from the masked rows is dropped.
  const maskDropped = maskOpen && !subsetExactFor(mask);

  if (sampleSet == null) return null; // mode off — nothing to control

  const universe = measuredUniverse(rounds);
  const roundSets = roundMeasuredSets(rounds);
  const inSet = new Set(sampleSet);
  const coverage = roundsCoveringSample(rounds);
  const fullyCovered = roundSets.length;
  // Served: the cells every member of the adopted line answered — stronger than round coverage.
  const shared = new Set(overlap?.sample_ids ?? []);

  return (
    <div className="ss-control">
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
            // Three facts, three channels (fill, opacity, underline) — more than a `Chip` carries.
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
