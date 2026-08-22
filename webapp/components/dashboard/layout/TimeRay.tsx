"use client";
// THE TIME-RAY — the absolute linear sequence of what happened, above the optimizer.
//
// Every other axis on this page answers a different question and none of them answers this
// one. `TopStrip` is current scalars with no time in it. `RoundAxis` selects a ROUND; the ray
// selects an EVENT, and its round steps write the SAME `SelectionContext.round`, so the two
// visibly agree rather than competing — one axis, two entry points. `DendrogramStrip` is
// structural genealogy on the bars' x-axis, not temporal at all.
//
// Full-bleed on purpose: it is NOT inside `DashSpine`. The spine is a 980px stripe that also
// translates itself left by half the sidebar width, and a chronology clipped to that reads as
// a widget rather than an axis.
//
// Steps are EVEN — one width per event, regardless of how long it took — with explicit gap
// markers for the silences. Time is not the x-axis; sequence is. A real time axis would
// squash a whole night's work into a pixel and stretch one lunch break across the screen.

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

  // The newest event is the one you came to read, so the track opens at its RIGHT end and
  // stays there as events land. It follows only while following the head: once a moment is
  // picked, the operator owns the scroll and yanking it back would fight the gesture that
  // set it. Scroll position is not derivable state — nothing but the DOM holds it.
  const trackRef = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    const el = trackRef.current;
    if (!el || at !== null) return;
    el.scrollLeft = el.scrollWidth;
  }, [steps.length, at]);

  // Run-phase comes off the VIEWED cycle's dashboard poll — the one server-owned run state
  // (I6). The ray adds the other input the server cannot have: whether anything is actually
  // progressing. Nothing here re-derives "is it running".
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

  // A step's click MOVES THE PAGE TO IT. Which axes it moves is read off the step, and every
  // one of them already exists:
  //   • the MOMENT — a step on this course names an offset in this course's ledger, so the
  //     whole dashboard re-folds to it and every panel reports what was true then. A step
  //     below (a fork, an inner run) counts in another ledger, so it returns this course to
  //     its head instead of pretending its number means something here.
  //   • a candidate step → resolve the node off the served tree and fire the shared
  //     navigate+inspect gesture. Resolved rather than constructed because
  //     `SelectedCandidate` carries `accuracy`/`is_winner`, which a ray item cannot honestly
  //     supply and which `ScoringInspector` renders.
  //   • a round step → the shared round axis, so `RoundAxis` moves with it.
  //   • anything else in another cycle → re-root the dashboard there (one verb handles a
  //     fork and an inner run alike, because it takes a whole path).
  const onStep = (step: RayStep): void => {
    const elsewhere = step.pathKey !== rootKey;
    setAt(elsewhere ? null : step.offset);
    if (step.candidateLabel) {
      // Join on `course_label`, the MINTING course's private position. `candidate_id` is
      // re-minted on every re-run, so an id join silently misses — and a fork's contribution
      // wears a renumbered `label` on this timeline while its own ledger speaks the old one.
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

// A silence, not a step: non-interactive, so it never takes a tab stop on the way to the
// next event. `fmtGap` renders "" below the 90 s threshold, which is what makes a
// heartbeated 120 s backend query show no marker at all.
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
  // Lit only on this course: an offset from another ledger can coincide numerically with
  // one of ours and mean nothing at all.
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

// The head cap — where the ray points NOW. Sticky at the right edge, because the newest event
// is the one you came to read.
//
// It pairs a word with a shape/hatch and never relies on colour alone. `wedged` is the one
// state with no `phase-*` peer in the shared stylesheet, and deliberately so: it is NOT a
// `RunPhase` member, it is a ray-local reading of "the server still says running, and nothing
// has progressed". Giving it a `phase-` class would imply the server can produce it.
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
