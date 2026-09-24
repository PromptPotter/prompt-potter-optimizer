"use client";
import { useState } from "react";
import { cx } from "@/lib/cx";
import { measurementNode } from "@/lib/derivations";
import { runPhaseLabel } from "@/lib/run-phase";
import { useDashboard } from "@/lib/hooks/useDashboard";
import { useRoundNodes } from "@/lib/hooks/useRoundNodes";
import {
  Button,
  CopyButton,
  Dialog,
  IconSliders,
  Toolbar,
  ToolbarSpacer,
} from "@/components/ui";
import { PipelineFlow } from "@/components/dashboard/pipeline/PipelineFlow";
import { MechanismsPanel } from "@/components/dashboard/control/MechanismsPanel";
import { RoundAxis } from "./RoundAxis";
import type { PipelineDoc } from "./types";

// The Optimizer card: the loop's frame, round axis and liveness around the shared `PipelineFlow`;
// it draws no graph of its own. Mechanism toggles are read-only here and sit behind the header's last icon.

interface Props {
  pipeline: PipelineDoc | null;
}

export function OptimizerCard({ pipeline }: Props) {
  const [mechanismsOpen, setMechanismsOpen] = useState(false);
  // Liveness off the cycle stream's poll age: a frozen campaign still has a `dash`.
  const { dash, isLive } = useDashboard();
  const view = pipeline?.view ?? null;
  const activeId = dash?.current_round.active_node ?? null;
  const {
    nodes: roundNodes,
    round: viewedRound,
    showsCurrent: viewingLive,
    loading: nodesLoading,
  } = useRoundNodes();

  const nodeLabel: Record<string, string> = Object.fromEntries(
    (view?.nodes ?? []).map((n) => [n.id, n.label]),
  );
  const activeLabel = isLive && viewingLive && activeId ? nodeLabel[activeId] : null;
  // The RUN's state, not the connection's; `isLive` still gates the pulse on stale data.
  const runIsRunning = viewingLive && dash?.run_phase === "running";
  // The SERVER's phase, never a local "idle" — that word would cover paused, held and dead alike.
  const status = !dash
    ? "pending"
    : !viewingLive
      ? `round ${viewedRound}`
      : isLive
        ? activeLabel
          ? `live · ${activeLabel}`
          : "live"
        : runPhaseLabel(dash.run_phase, dash.stop_reason);

  // Off the audit twin: a node can be configured for a model and not have fired at all.
  const models = {
    by: Object.fromEntries(
      (view?.nodes ?? []).map((n) => [n.id, roundNodes[n.id]?.model ?? null]),
    ),
    loading: nodesLoading,
  };

  return (
    <div className={cx("workflow-card", runIsRunning && "running")}>
      <Toolbar className="workflow-toolbar">
        <span className="workflow-title">Optimizer</span>
        <RoundAxis />
        <span
          className="workflow-status"
          style={{ color: runIsRunning ? "var(--color-success)" : "var(--color-text-secondary)" }}
          aria-live="polite"
        >
          ● {status}
        </span>
        <ToolbarSpacer />
        {/* Off until a node has run — an empty `{}` copy reads as broken. */}
        <CopyButton
          data={roundNodes}
          disabled={Object.keys(roundNodes).length === 0}
          title={
            Object.keys(roundNodes).length === 0
              ? "No node of this round has finished yet"
              : "Copy the viewed round's nodes as JSON"
          }
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
          <IconSliders />
        </Button>
      </Toolbar>
      <div className="workflow-graph">
        <PipelineFlow
          bare
          view={view}
          status={pipeline ? "ok" : "loading"}
          connector={null}
          reach={pipeline?.reach ?? null}
          scope="optimizer"
          // One level drawn, yet `l1_score` still runs the whole campaign pipeline.
          nestsNode={measurementNode(pipeline)}
          activeNode={isLive && viewingLive ? activeId : null}
          isLive={isLive}
          tone="neutral"
          models={models}
        />
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
