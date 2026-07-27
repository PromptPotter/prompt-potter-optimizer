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
  // Single-node pipeline (TermNorm `llm_only`): the lone node is already shown by
  // TargetPipelineHero's SingleNodeChip, and the per-node `→`-chain + optimizer-lock
  // badge this list exists for are multi-node mechanics. Showing it for one node is
  // a redundant row carrying a lock that makes no sense on a single node (the
  // optimizer must be free to tune it) — so hide the whole list at ≤1 node.
  if (nodes.length <= 1) return null;
  return (
    <ol className="pipeline-node-list" aria-label="Pipeline nodes">
      {nodes.map((n, i) => {
        // Optimizer-locked = the optimizer may move nothing on this node: it has a
        // served param list and no param in it is tunable (a paramless node like
        // cache_lookup is `[].every` → locked too). web_search / entity_profiling
        // keep tunable params → open. Null schema (demo / not-yet-loaded) → no
        // badge, not a false "open".
        //
        // This sums a per-param flag into a per-NODE fact, so it is only true while
        // the served list stays COMPLETE (`NodeConfigParam`). It used to be the
        // widget list: prose and nested params were dropped at the server, so
        // pp-self's four meta-prompt nodes — whose whole search space is prose —
        // summed to `[].every` and wore a padlock while the optimizer rewrote them.
        const params = schema?.[n.id];
        const locked = params != null && params.every((p) => !p.optimizer_tunable);
        const isSelected = selected?.scope === "target" && selected.id === n.id;
        return (
          <li key={n.id}>
            {i > 0 ? <span aria-hidden="true">→</span> : null}
            <button
              type="button"
              aria-pressed={isSelected}
              onClick={() =>
                setSelectionForNode(isSelected ? null : { id: n.id, scope: "target" })
              }
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
