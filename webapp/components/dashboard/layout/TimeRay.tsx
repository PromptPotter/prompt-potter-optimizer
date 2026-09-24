"use client";
// THE TIME-RAY: every event in sequence, one even step each — sequence is the x-axis, not time.
// Full-bleed, outside `DashSpine`; its round steps write the same `SelectionContext.round` as `RoundAxis`.

import { memo, useEffect, useMemo, useRef } from "react";
import { cx } from "@/lib/cx";
import { fmtGap } from "@/lib/format";
import { runPhaseLabel } from "@/lib/run-phase";
import { useWorkspace } from "@/lib/workspace";
import { useSelection } from "@/lib/SelectionContext";
import { useDashboard } from "@/lib/hooks/useDashboard";
import { useTimeRay } from "@/lib/poll";
import { useSelectNode } from "@/lib/hooks/useSelectNode";
import { useViewedLineage } from "@/lib/lineage";
import { rayHead, raySteps, type RayStep } from "@/lib/derivations";
import { encodeCyclePath } from "@/lib/ids";

export const TimeRay = memo(function TimeRay() {
  const { viewedPath, selectCyclePath } = useWorkspace();
  const { setSelectionForRound } = useSelection();
  const { index } = useViewedLineage();
  const { dash, at } = useDashboard();
  const { pick } = useSelectNode(selectCyclePath);
  const { items, loaded, failed, hasMore, loadOlder, nowMs, setAt } = useTimeRay();

  const rootKey = viewedPath ? encodeCyclePath(viewedPath) : "";
  const steps = useMemo(() => raySteps(items, rootKey), [items, rootKey]);

  // Pinned to the newest event only while following the head; once a moment is picked, the
  // operator owns the scroll.
  const trackRef = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    const el = trackRef.current;
    if (!el || at !== null) return;
    el.scrollLeft = el.scrollWidth;
  }, [steps.length, at]);

  // Run-phase is the server's (I6); the ray adds only whether anything is progressing.
  const head = useMemo(
    () =>
      rayHead(
        steps,
        items,
        dash?.run_phase,
        runPhaseLabel(dash?.run_phase, dash?.stop_reason),
        nowMs,
        rootKey,
      ),
    [steps, items, dash?.run_phase, dash?.stop_reason, nowMs, rootKey],
  );

  if (!viewedPath || (!loaded && steps.length === 0)) return null;

  // A step on another course's ledger returns this course to its head — its offset means nothing
  // here. A candidate resolves off the served tree: a ray item cannot supply `accuracy`/`is_winner`.
  const onStep = (step: RayStep): void => {
    const elsewhere = step.pathKey !== rootKey;
    setAt(elsewhere ? null : step.offset);
    if (step.candidateLabel) {
      // Join on `course_label`: `candidate_id` is re-minted per re-run, and a fork's `label` is
      // renumbered on this timeline.
      const node = index
        .get(step.pathKey)
        ?.candidates.find((c) => c.course_label === step.candidateLabel);
      if (node) {
        pick(node, step.path);
        return;
      }
    }
    if (elsewhere) selectCyclePath(step.path, null);
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
      <HeadCap head={head} onGo={() => head.target && selectCyclePath(head.target, null)} />
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
      title={`Nothing was recorded anywhere in this family for ${label}. The 15 s heartbeat rides the ray as proof-of-life, so this is real silence, not a long call.`}
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
  const when = new Date(step.at).toLocaleString();
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
            `tone-${step.activity.tone ?? "muted"}`,
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

// `wedged` is ray-local, not a `RunPhase` member — a `phase-` class would imply the server can
// produce it.
function HeadCap({
  head,
  onGo,
}: {
  head: ReturnType<typeof rayHead>;
  onGo: () => void;
}) {
  const body = (
    <>
      <span className="dash-time-ray-head-label">{head.label}</span>
      {head.detail && <span className="dash-time-ray-head-detail">{head.detail}</span>}
    </>
  );
  const className = cx(
    "dash-time-ray-head",
    head.state === "wedged" ? "ray-head-wedged" : `phase-${head.state}`,
  );
  if (!head.target) {
    return (
      <span className={className} title="The head of the ray — what this run is doing now.">
        {body}
      </span>
    );
  }
  return (
    <button
      type="button"
      className={className}
      onClick={onGo}
      title="The newest activity is in a child run — open it."
    >
      {body}
      <span aria-hidden="true">↳</span>
    </button>
  );
}
