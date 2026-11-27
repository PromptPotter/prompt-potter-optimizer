import { useState } from "react";
import { cx } from "@/lib/cx";
import { figureOf } from "@/lib/derivations";
import { useViewedCycle } from "@/lib/state/lineage";
import { useViewedDashboard } from "@/lib/state/poll";
import { useConnector } from "@/lib/hooks/useConnector";
import { shownData } from "@/lib/hooks/useRead";
import { useRoundNodes } from "@/lib/hooks/useRoundNodes";
import {
  Button,
  CopyButton,
  Dialog,
  HoverCard,
  IconSliders,
  Toolbar,
  ToolbarSpacer,
} from "@/components/ui";
import { PipelineFlow } from "@/components/pipeline/PipelineFlow";
import { NodeKnobsPanel } from "@/components/pipeline/NodeKnobsPanel";
import { KnobClashes, useKnobClashes } from "./KnobClashes";
import { RaceCatchUp } from "./RaceCatchUp";
import { RoundAxis } from "./RoundAxis";

export function OptimizerCard() {
  const [knobsOpen, setKnobsOpen] = useState(false);
  const clashes = useKnobClashes();
  const clashCount = clashes.active?.length ?? 0;
  const { dash, isLive } = useViewedDashboard();
  const runState = figureOf(useViewedCycle(), "state");
  const active = dash?.active ?? null;
  const { nodes: roundNodes } = useRoundNodes();
  const read = useConnector();
  const optimizerSetup = shownData(read)?.optimizer_setup ?? null;

  // One predicate, `isLive`, for its class and its glow (the served `active` is null unless
  // appending). What the round axis beside it does not already say: where the line runs on, the node firing,
  // else the SERVER's phase (never a local "idle": that word covers paused, held and dead alike).
  const status = !dash
    ? null
    : (dash.successor?.label ?? (isLive ? (active?.node ?? null) : (runState?.value ?? null)));

  return (
    <div className={cx("workflow-card", isLive && "running")}>
      <Toolbar className="workflow-toolbar">
        <span className="workflow-title">Optimizer</span>
        <RoundAxis />
        <span className={cx("workflow-status", isLive && "is-running")} aria-live="polite">
          {status}
        </span>
        <ToolbarSpacer />
        {Object.keys(roundNodes).length === 0 ? (
          <HoverCard content="No node of this round has finished yet">
            <CopyButton data={roundNodes} disabled title="Copy the viewed round's nodes as JSON" />
          </HoverCard>
        ) : (
          <CopyButton data={roundNodes} title="Copy the viewed round's nodes as JSON" />
        )}
        <Button
          variant="ghost"
          className={cx("workflow-mech", clashCount > 0 && "has-clash")}
          aria-haspopup="dialog"
          aria-expanded={knobsOpen}
          aria-label={
            clashes.read.status === "failed"
              ? "Optimizer knobs — the clash check did not load"
              : clashCount > 0
                ? `Optimizer knobs — ${clashCount} clash${clashCount === 1 ? "" : "es"} in this config`
                : "Optimizer knobs"
          }
          title="Optimizer knobs"
          onClick={() => setKnobsOpen(true)}
        >
          <IconSliders />
          {clashCount > 0 ? <span className="workflow-mech-count">{clashCount}</span> : null}
        </Button>
      </Toolbar>
      <div className="workflow-graph">
        <PipelineFlow
          bare
          layer={{
            view: optimizerSetup?.view ?? null,
            read,
            connector: null,
            reach: optimizerSetup?.reach ?? null,
            scope: "optimizer",
            nestsNode: optimizerSetup?.measurement_node ?? null,
            active,
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
