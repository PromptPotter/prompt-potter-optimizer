"use client";
import { useState, type ReactNode } from "react";
import { pipelineReadStatus } from "@/lib/derivations";
import { useConnector } from "@/lib/hooks/useConnector";
import { useCycleStream } from "@/lib/poll";
import { useNestedPipelines } from "@/lib/hooks/useNestedPipelines";
import { useOptimizerPipeline } from "@/lib/hooks/useOptimizerPipeline";
import type { NodeReach } from "@/lib/api";
import type { NodeScope } from "@/lib/SelectionContext";
import type { PipelineView } from "@/lib/types";
import type { PipelineStatus } from "@/lib/types";
import { ConnectorInspector } from "./ConnectorInspector";
import { PipelineFlow } from "./PipelineFlow";

interface Layer {
  key: string;
  // Not the connector name — pp-self's target and the optimizer both report "PromptPotter".
  label: string;
  view: PipelineView | null;
  status: PipelineStatus;
  connector: string | null;
  reach: Record<string, NodeReach> | null;
  scope: NodeScope | null;
  nestsNode: string | null;
  activeNode: string | null;
  isLive: boolean;
}

function ZoomGlyph({ depth }: { depth: number }) {
  const pitch = 4;
  const top = (16 - (depth * pitch - 2)) / 2;
  return (
    <svg viewBox="0 0 16 16" width="13" height="13" fill="currentColor" aria-hidden="true">
      {Array.from({ length: depth }, (_, i) => (
        <rect key={i} x={3} y={top + i * pitch} width={10} height={2} rx={1} />
      ))}
    </svg>
  );
}

interface Props {
  datasetName: string | null;
}

const CAMPAIGN_LEVEL = 1;

export function PipelineStack({ datasetName }: Props) {
  const cv = useConnector();
  const { dash, isLive } = useCycleStream();
  const [outermost, setOutermost] = useState(CAMPAIGN_LEVEL);
  // Gated on the campaign pipeline resolving, so an anon preview fires nothing.
  const nested = useNestedPipelines(cv.nests, cv.schema.status === "ok");
  const { doc: optimizer, loading: optimizerLoading } = useOptimizerPipeline(
    outermost === 0 ? cv.optimizer : null,
  );
  const activeNode = dash?.current_round.active_node ?? null;

  const layers: Layer[] = [
    {
      key: "optimizer",
      label: cv.optimizer ? `the ${cv.optimizer} optimization loop` : "the optimization loop",
      view: optimizer?.view ?? null,
      status: pipelineReadStatus({
        bound: cv.schema.status !== "unbound",
        loading: optimizerLoading || cv.schema.status === "loading",
        failed: !optimizer,
      }),
      connector: "PromptPotter",
      reach: optimizer?.reach ?? null,
      scope: "optimizer",
      nestsNode: optimizer?.measurement_node ?? null,
      activeNode,
      isLive,
    },
    {
      key: "campaign",
      label: datasetName ?? "this campaign's pipeline",
      view: cv.view,
      status: cv.schema.status,
      connector: cv.connector,
      reach: cv.reach,
      scope: "target",
      nestsNode: cv.nests?.node ?? null,
      activeNode,
      isLive,
    },
    ...nested.layers.map((l) => ({
      key: l.dataset,
      label: l.dataset,
      view: l.view,
      status: l.status,
      connector: l.connector,
      reach: l.reach,
      // Read-only: an id collision in another dataset's namespace would light a node not running.
      scope: null,
      nestsNode: l.nestsNode,
      activeNode: null,
      isLive: false,
    })),
  ];
  const anchor = layers.length - 1;
  // Indices count from the OUTSIDE in, so a choice survives deeper levels resolving.
  const start = Math.min(outermost, anchor);

  const zoomStrip = (
    <div className="pipeline-zoom">
      {layers.slice(0, anchor).map((l, i) => {
        const shown = i >= start;
        return (
          <button
            key={l.key}
            type="button"
            className="pipeline-zoom-btn"
            aria-controls="pipeline-stack"
            aria-pressed={shown}
            aria-label={`Show ${l.label}`}
            title={`${shown ? "Hide" : "Show"} ${l.label}`}
            onClick={() => setOutermost(shown ? i + 1 : i)}
          >
            <ZoomGlyph depth={anchor - i + 1} />
          </button>
        );
      })}
    </div>
  );

  const draw = (i: number): ReactNode => {
    const l = layers[i];
    if (!l) return null;
    return (
      <PipelineFlow
        key={l.key}
        view={l.view}
        status={l.status}
        connector={l.connector}
        reach={l.reach}
        scope={l.scope}
        nestsNode={l.nestsNode}
        activeNode={l.activeNode}
        isLive={l.isLive}
        inspector={i === anchor ? <ConnectorInspector view={cv} /> : undefined}
        nested={i < anchor ? draw(i + 1) : undefined}
        tone={(anchor - i) % 2 === 0 ? "accent" : "neutral"}
      />
    );
  };

  return (
    <div className="pipeline-stack" id="pipeline-stack">
      {zoomStrip}
      {draw(start)}
      {nested.truncated && (
        <p className="pipeline-stack-note">Stack incomplete — {nested.truncated}.</p>
      )}
    </div>
  );
}
