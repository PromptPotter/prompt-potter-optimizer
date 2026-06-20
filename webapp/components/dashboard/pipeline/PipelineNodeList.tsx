"use client";
import { useConnector } from "@/lib/hooks/useConnector";
import { useSelection } from "@/lib/SelectionContext";

// Vanilla, extendable inline list of the active pipeline's nodes — the plain
// "business mechanics" view (a simpler stand-in for the glassmorphic hero strip).
// Each node is a button that selects it, opening its per-kind NodeSurface via the
// same SelectionContext.node axis the hero writes. It maps straight over the
// served view nodes, so adding a node to the pipeline just shows up here — no
// per-node code. Intentionally near-unstyled; this is mechanics, not polish.
//
// Self-sources the connector view from the nearest `ConnectorProvider`, so it
// reads the shell connector on the Chat tab and the draft's nested connector
// inside the ingest setup section — the same component, the right scope each.
export function PipelineNodeList() {
  const cv = useConnector();
  const { node: selected, setSelectionForNode } = useSelection();
  const nodes = (cv.view?.nodes ?? []).filter((n) => n.kind !== "io");
  const schema = cv.nodeConfigSchema;
  if (nodes.length === 0) return null;
  return (
    <ol className="pipeline-node-list" aria-label="Pipeline nodes">
      {nodes.map((n, i) => {
        // Optimizer-locked = the optimizer may move nothing on this node: it has
        // a served config schema and no param in it is tunable (a paramless node
        // like cache_lookup is `[].every` → locked too). web_search /
        // entity_profiling keep tunable params → open. Mirror the lock the
        // search-space config editor shows inside, on the collapsed toggle. Null schema
        // (demo / not-yet-loaded) → no badge, not a false "open".
        const params = schema?.[n.id];
        const locked = params != null && params.every((p) => !p.optimizer_tunable);
        return (
          <li key={n.id}>
            {i > 0 ? <span aria-hidden="true">→</span> : null}
            <button
              type="button"
              aria-pressed={selected === n.id}
              onClick={() => setSelectionForNode(selected === n.id ? null : n.id)}
            >
              {locked ? (
                <span className="pnl-lock" title="Optimizer-locked" aria-label="optimizer-locked">
                  🔒
                </span>
              ) : null}
              {n.label}
              <small> ({n.kind ?? "node"})</small>
            </button>
          </li>
        );
      })}
    </ol>
  );
}
