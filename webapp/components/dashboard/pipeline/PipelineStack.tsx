import { useState, type ReactNode } from "react";
import type { NestedPipelineRef } from "@/lib/api/types";
import { BRAND } from "@/lib/brand";
import { useConnector } from "@/lib/hooks/useConnector";
import { shownData } from "@/lib/hooks/useRead";
import { useViewedDashboard } from "@/lib/poll";
import { CAMPAIGN_LAYER, useNestedPipelines } from "@/lib/hooks/useNestedPipelines";
import { ConnectorInspector } from "./ConnectorInspector";
import { PipelineFlow, type PipelineLayer } from "./PipelineFlow";

interface Layer extends PipelineLayer {
  key: string;
  // The layer it nests in; a level holds one layer per dataset the level above measures.
  parent: string | null;
  level: number;
  // Not the connector: pp-self's target runs on the optimizer's own.
  label: string;
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
const NO_NESTS: NestedPipelineRef[] = [];

export function PipelineStack({ datasetName }: Props) {
  const read = useConnector();
  const served = shownData(read);
  const nests = served?.nests ?? NO_NESTS;
  const { dash, isLive } = useViewedDashboard();
  const [outermost, setOutermost] = useState(CAMPAIGN_LEVEL);
  // Gated on the campaign pipeline resolving, so an anon preview fires nothing.
  const nested = useNestedPipelines(nests, served !== null);
  const activeNode = dash?.current_round.active_node ?? null;

  const layers: Layer[] = [
    {
      key: "optimizer",
      parent: null,
      level: 0,
      label: served ? `the ${served.optimizer} optimization loop` : "the optimization loop",
      view: served?.optimizer_setup.view ?? null,
      read,
      connector: BRAND.provider.name,
      reach: served?.optimizer_setup.reach ?? null,
      scope: "optimizer",
      nestsNode: served?.optimizer_setup.measurement_node ?? null,
      activeNode,
      isLive,
    },
    {
      key: CAMPAIGN_LAYER,
      parent: "optimizer",
      level: CAMPAIGN_LEVEL,
      label: datasetName ?? "this campaign's pipeline",
      view: served?.view ?? null,
      read,
      connector: served?.connector ?? null,
      reach: served?.reach ?? null,
      scope: "target",
      nestsNode: nests[0]?.node ?? null,
      activeNode,
      isLive,
    },
    ...nested.layers.map((l) => ({
      key: l.key,
      parent: l.parent,
      level: CAMPAIGN_LEVEL + 1 + l.depth,
      label: l.dataset,
      view: l.view,
      read: l.read,
      connector: l.connector,
      reach: l.reach,
      // Read-only: an id collision in another dataset's namespace would light a node not running.
      scope: null,
      nestsNode: l.nestsNode,
      activeNode: null,
      isLive: false,
    })),
  ];
  const anchor = Math.max(...layers.map((l) => l.level));
  // Levels count from the OUTSIDE in, so a choice survives deeper levels resolving.
  const start = Math.min(outermost, anchor);
  const at = (level: number) => layers.filter((l) => l.level === level);
  const inspected = at(anchor)[0]?.key;

  const zoomStrip = (
    <div className="pipeline-zoom">
      {Array.from({ length: anchor }, (_, level) => {
        const shown = level >= start;
        const named = at(level);
        const label = named.length === 1 ? named[0]!.label : `${named.length} datasets`;
        return (
          <button
            key={level}
            type="button"
            className="pipeline-zoom-btn"
            aria-controls="pipeline-stack"
            aria-pressed={shown}
            aria-label={`Show ${label}`}
            title={`${shown ? "Hide" : "Show"} ${label}`}
            onClick={() => setOutermost(shown ? level + 1 : level)}
          >
            <ZoomGlyph depth={anchor - level + 1} />
          </button>
        );
      })}
    </div>
  );

  const draw = (l: Layer): ReactNode => {
    const inner = layers.filter((c) => c.parent === l.key);
    return (
      <PipelineFlow
        key={l.key}
        layer={l}
        inspector={l.key === inspected ? <ConnectorInspector /> : undefined}
        nested={inner.length > 0 ? inner.map(draw) : undefined}
        tone={(anchor - l.level) % 2 === 0 ? "accent" : "neutral"}
      />
    );
  };

  return (
    <div className="pipeline-stack" id="pipeline-stack">
      {zoomStrip}
      {at(start).map(draw)}
      {nested.unread && (
        <p className="pipeline-stack-note">Stack incomplete — {nested.unread}.</p>
      )}
    </div>
  );
}
