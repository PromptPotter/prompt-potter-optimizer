"use client";

import { memo, useEffect, useMemo, useRef } from "react";
import { cx } from "@/lib/cx";
import { fmtGap } from "@/lib/format";
import { useWorkspace } from "@/lib/workspace";
import { useSelection } from "@/lib/SelectionContext";
import { useTimeRay } from "@/lib/poll";
import { useSelectNode } from "@/lib/hooks/useSelectNode";
import { useViewedLineage } from "@/lib/lineage";
import { armAt, raySteps, type RayStep } from "@/lib/derivations";
import { encodeCyclePath } from "@/lib/ids";

// The chronology alone: the run's state now is the masthead's STATE.
export const TimeRay = memo(function TimeRay() {
  const { viewedPath, viewedCandidateId, navigate, at } = useWorkspace();
  const { setSelectionForRound } = useSelection();
  const { index } = useViewedLineage();
  const { pick } = useSelectNode();
  const { steps: served, loaded, failed, hasMore, loadOlder } = useTimeRay();

  const rootKey = viewedPath ? encodeCyclePath(viewedPath) : "";
  const steps = useMemo(() => raySteps(served, rootKey), [served, rootKey]);

  const trackRef = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    const el = trackRef.current;
    if (!el || at !== null) return;
    el.scrollLeft = el.scrollWidth;
  }, [steps.length, at]);

  if (!viewedPath || (!loaded && steps.length === 0)) return null;
  const setAt = (offset: number | null) =>
    navigate({ path: viewedPath, candidateId: viewedCandidateId, at: offset });

  const onStep = (step: RayStep): void => {
    const elsewhere = step.pathKey !== rootKey;
    // Another course's ledger offset means nothing here: that step opens the other course at its head.
    if (!elsewhere) setAt(step.offset);
    if (step.arm) {
      const node = armAt(index, step.path, step.arm);
      if (node) {
        pick(node);
        return;
      }
    }
    if (elsewhere) navigate({ path: step.path });
    if (step.round != null) setSelectionForRound(step.round);
  };

  return (
    <section
      className="dash-time-ray"
      aria-label="Time-ray — everything that happened, in order. Pick a step to read the dashboard as it stood then."
    >
      <div className="dash-time-ray-track" role="list" ref={trackRef}>
        {hasMore && (
          <button
            type="button"
            className="dash-time-ray-more"
            onClick={loadOlder}
            title="Load the window of history before this one."
          >
            ‹ earlier
          </button>
        )}
        {failed && steps.length === 0 && (
          <span className="dash-time-ray-empty">Chronology unavailable</span>
        )}
        {!failed && loaded && steps.length === 0 && (
          <span className="dash-time-ray-empty">Nothing recorded yet</span>
        )}
        {steps.map((step) => (
          <Step
            key={step.key}
            step={step}
            rootKey={rootKey}
            viewedOffset={at}
            onPick={onStep}
          />
        ))}
        {at !== null && (
          <button
            type="button"
            className="dash-time-ray-now"
            onClick={() => setAt(null)}
            title="Stop replaying and follow the run again."
          >
            now ›
          </button>
        )}
      </div>
    </section>
  );
});

// `fmtGap` renders "" below 90 s, so a heartbeated backend query shows no marker.
function Gap({ seconds }: { seconds: number }) {
  const label = fmtGap(seconds);
  if (!label) return null;
  return (
    <span
      className="dash-time-ray-gap"
      title={`No step and no heartbeat anywhere in this family for ${label}. An open call beats every 15 s, so this is real silence, not a long call.`}
    >
      ── {label} ──
    </span>
  );
}

function Step({
  step,
  rootKey,
  viewedOffset,
  onPick,
}: {
  step: RayStep;
  rootKey: string;
  viewedOffset: number | null;
  onPick: (step: RayStep) => void;
}) {
  const elsewhere = step.pathKey !== rootKey;
  // Only on this course: another ledger's offset can coincide numerically with ours.
  const viewing = !elsewhere && viewedOffset === step.offset;
  const when = new Date(step.ts).toLocaleString();
  const where = elsewhere ? ` · in ${step.path[step.path.length - 1]?.cycleId ?? ""}` : "";
  const many = step.cluster > 1 ? ` · ${step.cluster} events here` : "";
  return (
    <>
      <Gap seconds={step.gapBeforeS} />
      <span role="listitem" className="dash-time-ray-slot">
        <button
          type="button"
          className={cx(
            "dash-time-ray-step",
            `tone-${step.activity.tone}`,
            elsewhere && "is-elsewhere",
            viewing && "is-viewing",
          )}
          aria-current={viewing ? "true" : undefined}
          onClick={() => onPick(step)}
          title={`${step.activity.label}${step.activity.detail ? ` — ${step.activity.detail}` : ""}\n${when}${where}${many}`}
        >
          <span aria-hidden="true">{step.activity.icon}</span>
          <span className="dash-time-ray-step-label">{step.activity.label}</span>
          {step.cluster > 1 && <span className="dash-time-ray-x">×{step.cluster}</span>}
        </button>
      </span>
    </>
  );
}
