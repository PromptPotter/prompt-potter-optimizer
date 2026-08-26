"use client";
import { useState, type ReactNode } from "react";
import { interiorNodes } from "@/lib/derivations";
import { useConnector } from "@/lib/hooks/useConnector";
import { useDashboard } from "@/lib/hooks/useDashboard";
import { useNestedPipelines } from "@/lib/hooks/useNestedPipelines";
import { useOptimizerPipeline } from "@/lib/hooks/useOptimizerPipeline";
import type { NodeConfigParam } from "@/lib/api";
import type { NodeScope } from "@/lib/SelectionContext";
import type { PipelineDoc, PipelineView } from "@/components/workflow";
import type { PipelineStatus } from "@/lib/types";
import { ConnectorInspector } from "./ConnectorInspector";
import { PipelineFlow } from "./PipelineFlow";

// The campaign as the nesting it actually is, over one zoom axis. The stack is a chain,
// outermost first — the optimization loop, this campaign's pipeline, then one level per
// served `nests` — so its depth is data, not code.
//
//   [ optimization loop ── l1_score runs ↓                    ]
//     [ promptpotter-self ── l1_score runs ↓             ]
//       INPUT → [ justlogic-d234 ] → OUTPUT                      ANCHOR
//
// The ANCHOR is the innermost level: always drawn, never buttoned, sole bearer of
// Input/Output and of full size. Zoom out with a button (one per UNDRAWN level, each
// jumping straight to its own depth); zoom back in through a level's nesting node, which
// drops everything above it. `outermost` is the whole of that state — nothing else about
// the chain is stored, since a zoom re-parents every flow and re-parented state dies.

// The optimizer has no parent to be named by, so its nesting node is derived the way the
// server derives `nests`: the measurement node is what runs something else.
function measurementNode(doc: PipelineDoc | null): string | null {
  return interiorNodes(doc?.view).find((n) => n.kind === "measurement")?.id ?? null;
}

interface Layer {
  key: string;
  // What this level's button says it will draw. Not the connector name — pp-self's target
  // and the optimizer above it both report "PromptPotter".
  label: string;
  view: PipelineView | null;
  status: PipelineStatus;
  connector: string | null;
  schema: Record<string, NodeConfigParam[]> | null;
  scope: NodeScope | null;
  nestsNode: string | null;
  activeNode: string | null;
  isLive: boolean;
}

// One bar per level the button draws: the glyph is the count, so adjacent buttons differ
// by shape rather than by tooltip.
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

// Which level the chat opens on — "what am I optimizing" is the glance-level question. On
// an ordinary campaign this is already the anchor.
const CAMPAIGN_LEVEL = 1;

export function PipelineStack({ datasetName, samplesOpen, onToggleSamples }: Props) {
  const cv = useConnector();
  const { dash } = useDashboard();
  const [outermost, setOutermost] = useState(CAMPAIGN_LEVEL);
  // Everything below the campaign's pipeline. Gated on it resolving, so an anon preview
  // fires nothing.
  const nested = useNestedPipelines(cv.nests, cv.pipelineStatus === "ok");
  // The level above it: a static manifest, no identity dependency, read only on zoom-out.
  const { doc: optimizer } = useOptimizerPipeline(outermost === 0);
  const activeNode = dash?.current_round.active_node ?? null;

  const layers: Layer[] = [
    {
      key: "optimizer",
      label: "the optimization loop",
      view: optimizer?.view ?? null,
      status: optimizer ? "ok" : "loading",
      connector: "PromptPotter",
      schema: optimizer?.node_config_schema ?? null,
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
      schema: cv.nodeConfigSchema,
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
      schema: l.schema,
      // Read-only: no detail panel is scoped to another dataset's namespace, and an id
      // collision here would light a node that is not running.
      scope: null,
      nestsNode: l.nestsNode,
      activeNode: null,
      isLive: false,
    })),
  ];
  const anchor = layers.length - 1;
  // Indices count from the OUTSIDE in, so a choice survives deeper levels resolving; the
  // clamp covers the window before the walk lands.
  const start = Math.min(outermost, anchor);

  // Rides the anchor's row, ahead of its Input end — the strip is the level ABOVE the
  // outermost drawn one, so it belongs beside the picture rather than on a row of its own.
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
        schema={l.schema}
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
