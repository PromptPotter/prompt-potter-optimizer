"use client";

import { memo, useEffect, useMemo, useRef } from "react";
import { cx } from "@/lib/cx";
import { fmtGap } from "@/lib/format";
import { useWorkspace } from "@/lib/workspace";
import { useSelection } from "@/lib/SelectionContext";
import { useCycleStream, useTimeRay } from "@/lib/poll";
import { useSelectNode } from "@/lib/hooks/useSelectNode";
import { useViewedLineage } from "@/lib/lineage";
import { rayHead, raySteps, type RayStep } from "@/lib/derivations";
import { encodeCyclePath } from "@/lib/ids";

export const TimeRay = memo(function TimeRay() {
  const { viewedPath, navigate, at, setAt } = useWorkspace();
  const { setSelectionForRound } = useSelection();
  const { index } = useViewedLineage();
  const { dash } = useCycleStream();
  const { pick } = useSelectNode();
  const { items, loaded, failed, hasMore, loadOlder } = useTimeRay();

  const rootKey = viewedPath ? encodeCyclePath(viewedPath) : "";
  const steps = useMemo(() => raySteps(items, rootKey), [items, rootKey]);

  const trackRef = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    const el = trackRef.current;
    if (!el || at !== null) return;
    el.scrollLeft = el.scrollWidth;
  }, [steps.length, at]);

  const waitingOn = dash?.waiting_on ?? null;
  const runPhase = dash?.run_phase ?? null;
  const status = dash?.status ?? null;
  const producer = dash?.producer ?? null;
  const head = useMemo(
    () =>
      rayHead(
        steps,
        items,
        runPhase && status && producer ? { run_phase: runPhase, status, producer } : null,
        rootKey,
        waitingOn,
      ),
    [steps, items, runPhase, status, producer, rootKey, waitingOn],
  );

  if (!viewedPath || (!loaded && steps.length === 0)) return null;

  const onStep = (step: RayStep): void => {
    const elsewhere = step.pathKey !== rootKey;
    // Another course's ledger offset means nothing here, so that step returns this course to its head.
    setAt(elsewhere ? null : step.offset);
    if (step.candidateLabel) {
      const node = index
        .get(step.pathKey)
        ?.candidates.find((c) => c.reading.arm.label === step.candidateLabel);
      if (node) {
        pick(node, step.path);
        return;
      }
    }
    if (elsewhere) navigate(step.path);
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
      <HeadCap head={head} onGo={() => head?.target && navigate(head.target)} />
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

function HeadCap({
  head,
  onGo,
}: {
  head: ReturnType<typeof rayHead>;
  onGo: () => void;
}) {
  const body = (
    <>
      <span className="dash-time-ray-head-label">{head ? head.label : "—"}</span>
      {head?.detail && <span className="dash-time-ray-head-detail">{head.detail}</span>}
    </>
  );
  const className = cx(
    "dash-time-ray-head",
    head && (head.wedged ? "ray-head-wedged" : `tone-${head.tone}`),
  );
  if (!head?.target) {
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
