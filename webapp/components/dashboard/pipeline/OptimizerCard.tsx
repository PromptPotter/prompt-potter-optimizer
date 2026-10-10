"use client";
import { useState } from "react";
import { cx } from "@/lib/cx";
import { phaseWalks } from "@/lib/run-phase";
import { useCycleStream } from "@/lib/poll";
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
import { NodeKnobsPanel } from "@/components/dashboard/pipeline/NodeKnobsPanel";
import { RoundAxis } from "./RoundAxis";
import type { PipelineDoc } from "@/lib/types";

interface Props {
  pipeline: PipelineDoc | null;
}

export function OptimizerCard({ pipeline }: Props) {
  const [knobsOpen, setKnobsOpen] = useState(false);
  const { dash, isLive } = useCycleStream();
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
  const runIsRunning = viewingLive && phaseWalks(dash?.run_phase);
  // The SERVER's phase, never a local "idle": that word covers paused, held and dead alike.
  const status = !dash
    ? "pending"
    : !viewingLive
      ? `round ${viewedRound}`
      : isLive
        ? activeLabel
          ? `live · ${activeLabel}`
          : "live"
        : dash.status.label;

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
          aria-expanded={knobsOpen}
          aria-label="Optimizer knobs"
          title="Optimizer knobs — each node's configuration for this campaign"
          onClick={() => setKnobsOpen(true)}
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
          nestsNode={pipeline?.measurement_node ?? null}
          activeNode={isLive && viewingLive ? activeId : null}
          isLive={isLive}
          tone="neutral"
          models={models}
        />
      </div>
      <Dialog
        open={knobsOpen}
        title="Optimizer knobs"
        onClose={() => setKnobsOpen(false)}
      >
        <p className="mech-lead">The manifest&apos;s values, with this campaign&apos;s overlay</p>
        <NodeKnobsPanel />
      </Dialog>
    </div>
  );
}
