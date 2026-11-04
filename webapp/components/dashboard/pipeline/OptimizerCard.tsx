import { useState } from "react";
import { cx } from "@/lib/cx";
import { figureOf, phaseWalks } from "@/lib/derivations";
import { useViewedCourse } from "@/lib/lineage";
import { useCycleStream } from "@/lib/poll";
import { useConnector } from "@/lib/hooks/useConnector";
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
import { KnobClashes, useKnobClashes } from "./KnobClashes";
import { RaceCatchUp } from "./RaceCatchUp";
import { RoundAxis } from "./RoundAxis";
import { useOptimizerPipeline } from "@/lib/hooks/useOptimizerPipeline";

export function OptimizerCard() {
  const { doc: pipeline, status: pipelineStatus } = useOptimizerPipeline();
  const [knobsOpen, setKnobsOpen] = useState(false);
  const clashes = useKnobClashes();
  const { dash, isLive } = useCycleStream();
  const runState = figureOf(useViewedCourse(), "state");
  const view = pipeline?.view ?? null;
  const activeId = dash?.current_round.active_node ?? null;
  const { nodes: roundNodes, showsCurrent: viewingLive } = useRoundNodes();
  const { optimizerSetup } = useConnector();

  const runIsRunning = viewingLive && phaseWalks(dash?.run_phase);
  // What the round axis beside it does not already say: the node firing, else the SERVER's phase
  // (never a local "idle": that word covers paused, held and dead alike).
  const status =
    !dash || !viewingLive
      ? null
      : isLive
        ? activeId
        : (runState?.value ?? null);

  return (
    <div className={cx("workflow-card", runIsRunning && "running")}>
      <Toolbar className="workflow-toolbar">
        <span className="workflow-title">Optimizer</span>
        <RoundAxis />
        <span className={cx("workflow-status", runIsRunning && "is-running")} aria-live="polite">
          {status}
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
          className={cx("workflow-mech", clashes.length > 0 && "has-clash")}
          aria-haspopup="dialog"
          aria-expanded={knobsOpen}
          aria-label={
            clashes.length > 0
              ? `Optimizer knobs — ${clashes.length} clash${clashes.length === 1 ? "" : "es"} in this config`
              : "Optimizer knobs"
          }
          title="Optimizer knobs — each node's configuration for this campaign"
          onClick={() => setKnobsOpen(true)}
        >
          <IconSliders />
          {clashes.length > 0 ? <span className="workflow-mech-count">{clashes.length}</span> : null}
        </Button>
      </Toolbar>
      <div className="workflow-graph">
        <PipelineFlow
          bare
          layer={{
            view,
            status: pipelineStatus,
            connector: null,
            reach: optimizerSetup?.reach ?? null,
            scope: "optimizer",
            nestsNode: pipeline?.measurement_node ?? null,
            activeNode: isLive && viewingLive ? activeId : null,
            isLive,
          }}
          tone="neutral"
        />
      </div>
      <RaceCatchUp />
      <Dialog
        open={knobsOpen}
        title="Optimizer knobs"
        onClose={() => setKnobsOpen(false)}
      >
        <p className="mech-lead">The manifest&apos;s values, with this campaign&apos;s overlay</p>
        <KnobClashes />
        <NodeKnobsPanel />
      </Dialog>
    </div>
  );
}
