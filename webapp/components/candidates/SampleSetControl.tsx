import { useState } from "react";
import type { MeasuredUnit, OverlapReading, SampleCoverage, ServedRound } from "@/lib/api/types";
import { fmtSampleTag, unitCount } from "@/lib/format";
import { cx } from "@/lib/cx";
import { useSelection } from "@/lib/state/selection";
import { useWorkspace } from "@/lib/state/workspace";
import { roundMeasuredSets, sameSampleSet, toggleInSet } from "@/lib/derivations";
import { Button, Chip, ChipGroup, HoverCard, SegmentedControl, type Segment } from "@/components/ui";
import { SampleTrajectorySeries } from "@/components/dashboard/samples/SampleTrajectory";

type LoadMode = "measured" | "planned";

const LOAD_MODES: readonly Segment<LoadMode>[] = [
  { value: "measured", label: "measured only" },
  { value: "planned", label: "+ planned" },
];

export function SampleSetControl({
  rounds,
  coverage,
  overlap,
  unit,
}: {
  rounds: ServedRound[];
  coverage: SampleCoverage | null;
  overlap: OverlapReading | null;
  unit: MeasuredUnit;
}) {
  const { sampleSet } = useSelection();
  const { setViewKeys } = useWorkspace();
  const [detailOpen, setDetailOpen] = useState(false);
  const [load, setLoad] = useState<LoadMode>("measured");

  if (sampleSet == null || coverage == null) return null;

  const universe = coverage.samples.map((s) => s.sample_id);
  const roundSets = roundMeasuredSets(rounds);
  const inSet = new Set(sampleSet);
  const of = coverage.measuring_rounds;
  const shared = new Set(overlap?.positions ?? []);

  return (
    <div className="ss-control">
      <div className="ss-strip">
        {coverage.samples.map(({ sample_id: sid, rounds: seen }) => {
          const on = inSet.has(sid);
          const everywhere = seen === of;
          const note =
            `Measured in ${seen}/${of} rounds` +
            (everywhere
              ? " — every bar can be read on it."
              : " — a bar for a round that never bought it is blank, not zero.") +
            (shared.has(sid) ? " On the served set: C0 and each new best since answered it." : "");
          return (
            // Three facts, three channels (fill, opacity, underline) — more than a `Chip` carries.
            <HoverCard
              key={sid}
              content={`Sample ${fmtSampleTag(sid)} — ${on ? "in" : "not in"} the overlap set. ${note}`}
            >
              <button
                type="button"
                className={cx("ss-cell", on && "on", everywhere && "everywhere", shared.has(sid) && "shared")}
                aria-pressed={on}
                aria-label={`Sample ${sid} — ${on ? "in" : "not in"} the overlap set. ${note}`}
                onClick={() => setViewKeys({ samples: toggleInSet(sampleSet, sid) })}
              >
                {sid}
              </button>
            </HoverCard>
          );
        })}
      </div>

      <div className="ss-row">
        <Button className="ss-action" onClick={() => setViewKeys({ samples: universe })}>
          All measured
        </Button>
        <HoverCard content="Deselect every sample and build the set up one at a time. Press ∩ above to close the picker.">
          <Button className="ss-action" onClick={() => setViewKeys({ samples: [] })}>
            Off
          </Button>
        </HoverCard>
        {overlap != null && (
          <HoverCard
            content={`The ${unitCount(overlap.positions.length, unit)} C0 and each new best since have all answered — the one basis they can be differenced on, and what the overlap bars sit on until you replace it.`}
          >
            <Chip
              on={sameSampleSet(sampleSet, overlap.positions)}
              onClick={() => setViewKeys({ samples: overlap.positions })}
            >
              overlap · {overlap.positions.length}
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
              onClick={() => setViewKeys({ samples: rs.ids })}
            >
              R{rs.round}
            </Chip>
          ))}
        </ChipGroup>
        <span className="ss-count">
          {sampleSet.length}/{universe.length}
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
