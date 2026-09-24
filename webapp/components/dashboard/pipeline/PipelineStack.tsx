"use client";
import { useState, type ReactNode } from "react";
import { measurementNode } from "@/lib/derivations";
import { useConnector } from "@/lib/hooks/useConnector";
import { useDashboard } from "@/lib/hooks/useDashboard";
import { useNestedPipelines } from "@/lib/hooks/useNestedPipelines";
import { useOptimizerPipeline } from "@/lib/hooks/useOptimizerPipeline";
import type { NodeReach } from "@/lib/api";
import type { NodeScope } from "@/lib/SelectionContext";
import type { PipelineView } from "@/components/workflow";
import type { PipelineStatus } from "@/lib/types";
import { ConnectorInspector } from "./ConnectorInspector";
import { PipelineFlow } from "./PipelineFlow";

// The campaign as its nesting chain, outermost first: optimization loop, this pipeline, one level
// per served `nests`. `outermost` is the only zoom state — a zoom re-parents every flow.

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
  samplesOpen: boolean;
  onToggleSamples: () => void;
}

const CAMPAIGN_LEVEL = 1;

export function PipelineStack({ datasetName, samplesOpen, onToggleSamples }: Props) {
  const cv = useConnector();
  const { dash } = useDashboard();
  const [outermost, setOutermost] = useState(CAMPAIGN_LEVEL);
  // Gated on the campaign pipeline resolving, so an anon preview fires nothing.
  const nested = useNestedPipelines(cv.nests, cv.pipelineStatus === "ok");
  const { doc: optimizer } = useOptimizerPipeline(outermost === 0);
  const activeNode = dash?.current_round.active_node ?? null;

  const layers: Layer[] = [
    {
      key: "optimizer",
      label: "the optimization loop",
      view: optimizer?.view ?? null,
      status: optimizer ? "ok" : "loading",
      connector: "PromptPotter",
      reach: optimizer?.reach ?? null,
      scope: "optimizer",
      nestsNode: measurementNode(optimizer ?? null),
      // The one level `active_node` speaks for.
      activeNode,
      isLive: cv.isLive,
    },
    {
      key: "campaign",
      label: datasetName ?? "this campaign's pipeline",
      view: cv.view,
      status: cv.pipelineStatus,
      connector: cv.connector,
      reach: cv.reach,
      scope: "target",
      nestsNode: cv.nests?.node ?? null,
      activeNode,
      isLive: cv.isLive,
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

  const zoomStrip =
    start > 0 ? (
      <div className="pipeline-zoom">
        {layers.slice(0, start).map((l, i) => (
          <button
            key={l.key}
            type="button"
            className="pipeline-zoom-btn"
            aria-controls="pipeline-stack"
            aria-label={`Show ${l.label}`}
            title={`Show ${l.label}`}
            onClick={() => setOutermost(i)}
          >
            <ZoomGlyph depth={anchor - i + 1} />
          </button>
        ))}
      </div>
    ) : null;

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
        leading={i === anchor ? zoomStrip : undefined}
        queryPath={
          i === anchor
            ? {
                pressed: samplesOpen,
                label: samplesOpen ? "Hide project preview" : "Show project preview",
                onClick: onToggleSamples,
                connector: <ConnectorInspector view={cv} />,
              }
            : undefined
        }
        nest={
          i < anchor
            ? { level: draw(i + 1), onIsolate: () => setOutermost(i + 1) }
            : undefined
        }
        tone={(anchor - i) % 2 === 0 ? "accent" : "neutral"}
      />
    );
  };

  return (
    <div className="pipeline-stack" id="pipeline-stack">
      {draw(start)}
      {/* A truncated recursion that looks finished is worse than a short one. */}
      {nested.truncated && (
        <p className="pipeline-stack-note">Stack incomplete — {nested.truncated}.</p>
      )}
    </div>
  );
}
