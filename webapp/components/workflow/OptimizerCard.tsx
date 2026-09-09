"use client";
import { useState } from "react";
import { cx } from "@/lib/cx";
import { measurementNode } from "@/lib/derivations";
import { runPhaseLabel } from "@/lib/run-phase";
import { useDashboard } from "@/lib/hooks/useDashboard";
import { useRoundNodes } from "@/lib/hooks/useRoundNodes";
import { Button, CopyButton, Dialog } from "@/components/ui";
import { PipelineFlow } from "@/components/dashboard/pipeline/PipelineFlow";
import { MechanismsPanel } from "@/components/dashboard/control/MechanismsPanel";
import { RoundAxis } from "./RoundAxis";
import type { PipelineDoc } from "./types";

// The Optimizer card: the loop's own frame, round axis and liveness, around the SHARED
// pipeline renderer. It draws no graph of its own — a hand-placed geometry here is a second
// answer to a shape the manifest already declares (`webapp/CLAUDE.md` § Component
// conventions).
//
// The mechanism toggles hang off this card rather than off the page, because they are
// policy of THIS loop and nothing else: `per_round_resubset` decides whether the round
// re-cuts its subset, the elimination toggles decide which arms PoBB kills. A page-level
// lane said only where the values are stored. Read-only here — the editable mode is
// ingest's, at authoring time.
//
// The trigger rides the CANVAS, not the toolbar: the toolbar is already four things
// competing for one glance (title, round axis, live status, copy), and a fifth word there
// buried the one that moves — the round and its state. On the dot grid beside the nodes it
// is an icon on the thing it configures.

interface Props {
  pipeline: PipelineDoc | null;
}

export function OptimizerCard({ pipeline }: Props) {
  const [mechanismsOpen, setMechanismsOpen] = useState(false);
  // Self-sourced liveness from the cycle stream (poll age), not `dash` truthiness — a
  // frozen campaign still has a `dash` snapshot but is not live.
  const { dash, isLive } = useDashboard();
  const view = pipeline?.view ?? null;
  const activeId = dash?.current_round.active_node ?? null;
  // The optimizer can only ever depict ONE round, so the round axis is this card's own
  // scope. Node selection rides the shared SelectionContext so `NodeDetail` opens below.
  const {
    nodes: roundNodes,
    round: viewedRound,
    showsCurrent: viewingLive,
    loading: nodesLoading,
  } = useRoundNodes();

  const nodeLabel: Record<string, string> = Object.fromEntries(
    (view?.nodes ?? []).map((n) => [n.id, n.label]),
  );
  // The active node's human label — the accessible, colour-independent echo of the pulse.
  // Gated on `viewingLive`: on a historical round there is no live node to name.
  const activeLabel = isLive && viewingLive && activeId ? nodeLabel[activeId] : null;
  // The card's own green: the RUN's state, not the connection's. Staleness is the
  // connection banner's job; `isLive` still gates the pulse, which must not animate stale data.
  const runIsRunning = viewingLive && dash?.run_phase === "running";
  // The phase the SERVER declares, never a local "idle" — that one word covers a paused
  // run, a run held at the origin gate and a dead producer alike.
  const status = !dash
    ? "pending"
    : !viewingLive
      ? `round ${viewedRound}`
      : isLive
        ? activeLabel
          ? `live · ${activeLabel}`
          : "live"
        : runPhaseLabel(dash.run_phase, dash.stop_reason);

  // What each node RAN this round, off the audit twin — the answer the config surface
  // cannot give, since a node can be configured for one model and have not fired at all.
  const models = {
    by: Object.fromEntries(
      (view?.nodes ?? []).map((n) => [n.id, roundNodes[n.id]?.model ?? null]),
    ),
    loading: nodesLoading,
  };

  return (
    <div className={cx("workflow-card", runIsRunning && "running")}>
      <div className="workflow-toolbar">
        <span className="workflow-title">Optimizer</span>
        <RoundAxis />
        <span
          style={{ color: runIsRunning ? "var(--color-success)" : "var(--color-text-secondary)" }}
          aria-live="polite"
        >
          ● {status}
        </span>
        <CopyButton data={roundNodes} title="Copy the viewed round's nodes as JSON" />
      </div>
      <div className="workflow-graph">
        <PipelineFlow
          bare
          view={view}
          status={pipeline ? "ok" : "loading"}
          connector={null}
          reach={pipeline?.reach ?? null}
          scope="optimizer"
          // The card draws ONE level, so there is nothing to zoom into — but `l1_score`
          // still runs the whole campaign pipeline, and saying so is the frame's job.
          nestsNode={measurementNode(pipeline)}
          activeNode={isLive && viewingLive ? activeId : null}
          isLive={isLive}
          tone="neutral"
          models={models}
        />
        <Button
          variant="ghost"
          className="workflow-mech"
          aria-haspopup="dialog"
          aria-expanded={mechanismsOpen}
          aria-label="Mechanisms"
          title="Mechanisms — pluggable sorting + early-abort toggles"
          onClick={() => setMechanismsOpen(true)}
        >
          <SlidersIcon />
        </Button>
      </div>
      <Dialog
        open={mechanismsOpen}
        title="Mechanisms"
        onClose={() => setMechanismsOpen(false)}
      >
        <p className="mech-lead">
          Pluggable sorting + early-abort toggles (campaign.json)
        </p>
        <MechanismsPanel />
      </Dialog>
    </div>
  );
}

// Module level, not a closure inside the card: a component defined during render is
// remounted on every poll tick.
function SlidersIcon() {
  return (
    <svg
      width="14"
      height="14"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M4 21v-7M4 10V3M12 21v-9M12 8V3M20 21v-5M20 12V3" />
      <path d="M1 14h6M9 8h6M17 16h6" />
    </svg>
  );
}
