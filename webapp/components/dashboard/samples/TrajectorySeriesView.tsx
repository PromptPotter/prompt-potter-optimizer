import { useMemo } from "react";
import { HoverCard, pressable } from "@/components/ui";
import { cx } from "@/lib/cx";
import { useSelection } from "@/lib/state/selection";
import { useWorkspace } from "@/lib/workspace";
import {
  MOVEMENT_CLASS,
  orderAtStep,
  sameSampleSet,
  seedFromOrder,
  type SelectMode,
  type SortedRounds,
  unionFirstAppearance,
} from "@/lib/derivations";
import { fmtSampleTag } from "@/lib/format";
import { SAMPLE_MOVEMENT_LABELS } from "@/lib/api/types";

interface Step {
  round: number;
  selection: number[];
  sampleId: number;
  position: number; // 1-indexed
  selectMode: SelectMode;
}

export function SeriesView({
  sorted,
  selectMode = "measured",
  maxHeight,
}: {
  sorted: SortedRounds;
  selectMode?: SelectMode;
  maxHeight?: number;
}) {
  const columns = useMemo(() => unionFirstAppearance(sorted.rounds), [sorted.rounds]);
  const { setViewKeys } = useWorkspace();

  return (
    <div
      className={cx("st-series", maxHeight != null && "scrollable")}
      style={maxHeight != null ? { maxHeight } : undefined}
    >
      <div className="st-series-inner">
        <div className="st-series-row">
          <span className="st-row-label">id</span>
          <span className="st-series-cells">
            {columns.map((sid) => (
              <span key={sid} className="st-sq st-col-header">{sid}</span>
            ))}
          </span>
        </div>
        {sorted.rounds.map((r, i) => {
          const pos = sorted.positions[i]!;
          const prev = i > 0 ? sorted.positions[i - 1]! : null;
          const movements = sorted.movements[i]!;
          const total = r.selection.length;
          return (
            <div key={r.round} className="st-series-row">
              <span className="st-row-label">R{r.round}</span>
              <span className="st-series-cells">
                {columns.map((sid) => {
                  const kind = movements.get(sid);
                  const p = pos.get(sid);
                  if (kind === undefined || p === undefined) {
                    return <span key={sid} className="st-sq absent">·</span>;
                  }
                  const pp = prev?.get(sid);
                  const titleNote =
                    pp !== undefined && pp !== p
                      ? `${SAMPLE_MOVEMENT_LABELS[kind]}: pos ${pp} → ${p}`
                      : SAMPLE_MOVEMENT_LABELS[kind];
                  const step: Step = { round: r.round, selection: r.selection, sampleId: sid, position: p, selectMode };
                  return (
                    <HoverCard key={sid} className="st-hover" content={<StepOrderCard step={step} />}>
                      <span
                        className={cx("st-sq", MOVEMENT_CLASS[kind], "st-series-cell")}
                        aria-label={`Sample ${sid}, round ${r.round}, position ${p} of ${total}. ${titleNote}. Hover for the sample order at this step; click to compare fitness on the samples up to here.`}
                        {...pressable(() =>
                          setViewKeys({ samples: seedFromOrder(orderAtStep(r.selection, sid, p), selectMode) }),
                        )}
                      >
                        {p}
                      </span>
                    </HoverCard>
                  );
                })}
              </span>
            </div>
          );
        })}
        <Legend />
      </div>
    </div>
  );
}

// The sample order at one step, read only while its card is open.
function StepOrderCard({ step }: { step: Step }) {
  const { sampleSet } = useSelection();
  const order = orderAtStep(step.selection, step.sampleId, step.position);
  const seedSet = seedFromOrder(order, step.selectMode);
  const isLoaded = sameSampleSet(sampleSet, seedSet);
  return (
    <>
      <div className="st-hover-head">
        R{step.round} · order at sample {fmtSampleTag(step.sampleId)} (step {step.position}/
        {step.selection.length})
      </div>
      <div className="st-hover-chips">
        {order.computed.map((sid) => (
          <span key={`c-${sid}`} className="st-chip computed" title={`${fmtSampleTag(sid)} — already computed`}>
            {sid}
          </span>
        ))}
        <span className="st-chip current" title={`${fmtSampleTag(order.current)} — measuring now`}>
          ▶{order.current}
        </span>
        {order.planned.map((sid) => (
          <span key={`p-${sid}`} className="st-chip planned" title={`${fmtSampleTag(sid)} — planned`}>
            {sid}
          </span>
        ))}
      </div>
      <div className="st-hover-foot">
        {order.computed.length} computed · {order.planned.length} planned —{" "}
        {isLoaded ? "loaded in fitness ✓" : `click to compare fitness on ${seedSet.length} samples`}
      </div>
    </>
  );
}

function Legend() {
  const swatch = (className: string, label: string) => (
    <span className="st-legend-item">
      <span className={cx("st-legend-swatch", className)} />
      <span className="st-legend-label">{label}</span>
    </span>
  );
  return (
    <div className="st-legend">
      {swatch("st-sq new", `${SAMPLE_MOVEMENT_LABELS.new} / ${SAMPLE_MOVEMENT_LABELS.readded}`)}
      {swatch(cx("st-sq", MOVEMENT_CLASS.gained), SAMPLE_MOVEMENT_LABELS.gained)}
      {swatch("st-sq lost", SAMPLE_MOVEMENT_LABELS.lost)}
      {swatch("st-sq kept", SAMPLE_MOVEMENT_LABELS.kept)}
      {swatch("st-sq absent", "not in bank")}
    </div>
  );
}
