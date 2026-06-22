"use client";
import type { PipelineView, PipelineViewNode } from "@/components/workflow";
import type { NodeConfigParam } from "@/lib/api";
import { ConnectorInspector } from "./ConnectorInspector";
import { useConnector } from "@/lib/hooks/useConnector";
import { useDashboard } from "@/lib/hooks/useDashboard";
import { useSelection } from "@/lib/SelectionContext";
import { liveObserveConfig } from "@/lib/derivations";
import { cx } from "@/lib/cx";

// The backend target LLM is being called throughout L1 scoring (and origin).
// `dash.state` cycles scoring → between_samples/between_candidates → scoring as
// each sample lands, so all four count as active or the node would flicker to
// "idle" between every sample. The optimizer phases (l1_generate / l2_refining /
// …) are NOT here — there the optimizer LLM runs and the backend sits idle.
const BACKEND_ACTIVE_PHASES = new Set([
  "scoring",
  "origin",
  "between_samples",
  "between_candidates",
]);

// The node's static origin model from the dataset overlay (the `model` param in
// the node-config schema) — the fallback when no live searchpoint is resolved.
// The live `current_round.nodes` map only ever carries OPTIMIZER node calls,
// never the backend target, so the running model comes from the in-flight
// candidate's served `resolved_pipeline_params` (see `liveModelResolver`).
function configuredModel(
  nodeId: string,
  nodeConfigSchema: Record<string, NodeConfigParam[]> | null,
): string | null {
  const m = nodeConfigSchema?.[nodeId]?.find((p) => p.key === "model")?.value;
  return typeof m === "string" && m ? m : null;
}

// Per-node model the chip shows when the backend is live: the in-flight
// candidate's server-resolved model, falling back to the static origin model.
// One resolver, built once in the hero and threaded to both chip layouts so
// 1-node and N-node datasets read the same source.
type LiveModelFor = (nodeId: string) => string | null;

function liveModelResolver(
  liveCfg: { config: Record<string, unknown> } | null,
  nodeConfigSchema: Record<string, NodeConfigParam[]> | null,
): LiveModelFor {
  return (nodeId) => {
    const nodeCfg = liveCfg?.config?.[nodeId];
    const m =
      nodeCfg && typeof nodeCfg === "object"
        ? (nodeCfg as Record<string, unknown>).model
        : null;
    return typeof m === "string" && m ? m : configuredModel(nodeId, nodeConfigSchema);
  };
}

interface Props {
  samplesOpen: boolean;
  onToggle: () => void;
}

const ATTACH_ICON = (
  <svg
    width="28"
    height="28"
    viewBox="0 0 20 20"
    fill="none"
    stroke="currentColor"
    strokeWidth="1.4"
    strokeLinecap="round"
    strokeLinejoin="round"
  >
    <polyline points="18 10 13 10 11.5 12.5 8.5 12.5 7 10 2 10" />
    <path d="M4.6 4.4 2 10v5a1.5 1.5 0 0 0 1.5 1.5h13a1.5 1.5 0 0 0 1.5-1.5v-5l-2.6-5.6a1.5 1.5 0 0 0-1.36-.9H5.96a1.5 1.5 0 0 0-1.36.9Z" />
  </svg>
);

const ANSWER_ICON = (
  <svg
    width="28"
    height="28"
    viewBox="0 0 20 20"
    fill="none"
    stroke="currentColor"
    strokeWidth="1.4"
    strokeLinecap="round"
    strokeLinejoin="round"
  >
    <path d="M5 2h6l4 4v11a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V3a1 1 0 0 1 1-1Z" />
    <path d="M11 2v5h4" />
    <path d="M6.5 12.5h6" />
    <path d="m10.5 10.5 2.5 2-2.5 2" />
  </svg>
);

const LLM_ICON = (
  <svg
    width="30"
    height="30"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="1.6"
    strokeLinecap="round"
    strokeLinejoin="round"
  >
    <path d="M12 2.5 6.5 18h11Z" fill="currentColor" fillOpacity="0.18" />
    <path d="M5 18c2.4 1.6 4.7 2 7 2s4.6-.4 7-2" />
    <path d="M5 18h14" />
    <path
      d="m13.6 8.4.55 1.55 1.55.55-1.55.55-.55 1.55-.55-1.55-1.55-.55 1.55-.55Z"
      fill="currentColor"
    />
    <circle cx="10.2" cy="13.2" r="0.7" fill="currentColor" />
  </svg>
);

// Synthetic input/output IDs the server emits in derive_pipeline_view —
// rendered as the flanking samples-toggle chips, not as graph dots.
function interiorNodes(view: PipelineView): PipelineViewNode[] {
  return view.nodes.filter((n) => n.kind !== "io");
}

// Single-node case: keep the original glassmorphic LLM chip. The label text
// stays "LLM" verbatim — even though the node id might be `llm_only`, the
// chip is a brand surface, not a dump of the wire identifier. Shows "idle" at
// rest; when the run is live AND the backend is actually being called (scoring/
// origin) it shows the configured model + an active pulse. Clickable — selects
// the node so BackendNodeDetail opens (same SelectionContext.node axis the
// multi-node strip writes).
function SingleNodeChip({
  node,
  liveModelFor,
  phase,
  isLive,
}: {
  node: PipelineViewNode;
  liveModelFor: LiveModelFor;
  phase: string | null;
  isLive: boolean;
}) {
  const { node: selected, setSelectionForNode: setSelected } = useSelection();
  const active = isLive && BACKEND_ACTIVE_PHASES.has(phase ?? "");
  const model = active ? (liveModelFor(node.id) ?? "running") : "idle";
  const isSelected = selected === node.id;
  return (
    <button
      type="button"
      className={cx("wf-hero-node", "llm", isSelected && "selected", active && "active")}
      aria-pressed={isSelected}
      aria-label={`Node: ${node.label}`}
      onClick={() => setSelected(isSelected ? null : node.id)}
    >
      <div className="head">
        <div className="ico">{LLM_ICON}</div>
        <div className="lbl">LLM</div>
      </div>
      <div className="val">{model}</div>
    </button>
  );
}

// No pipeline view loaded yet — the `/datasets/{name}/pipeline` fetch is in flight,
// failed, or no dataset is bound. NOT a real node: we must not fabricate a single
// "LLM" chip here (it would misrepresent a failed/loading fetch — or a real 5-node
// pipeline — as a genuine single-LLM pipeline). Neutral, non-clickable, no model value.
function PipelinePlaceholder() {
  return (
    <div className="wf-hero-node" aria-label="Pipeline unavailable" aria-busy="true">
      <div className="head">
        <div className="ico">{LLM_ICON}</div>
        <div className="lbl">Pipeline</div>
      </div>
      <div className="val">—</div>
    </div>
  );
}

// Multi-node strip: dots + outside labels + ribbon edges. Wrapped in the
// same glassmorphic `.wf-hero-node.llm` frame the single-LLM case uses,
// just wider, so 1-node and N-node datasets share one visual surface.
// Labels wrap on underscores (one `<tspan>` per part) to keep cell width
// compact for ids like `cache_lookup`.
function MultiNodeStrip({
  view,
  connector,
  liveModelFor,
  phase,
  isLive,
}: {
  view: PipelineView;
  connector: string | null;
  liveModelFor: LiveModelFor;
  phase: string | null;
  isLive: boolean;
}) {
  const { node: selected, setSelectionForNode: setSelected } = useSelection();
  const interior = interiorNodes(view);
  const CELL_W = 72;
  const CELL_H = 70;
  const RADIUS = 7;
  const cy = 14;
  const totalW = Math.max(CELL_W * interior.length, CELL_W);
  const cxFor = (i: number) => (i + 0.5) * CELL_W;

  // Ribbon between adjacent interior dots. Cubic Bézier with control
  // points pulled toward the midpoint vertically so the curve has a soft
  // sag rather than a straight tube.
  const edgePath = (i: number) => {
    const x1 = cxFor(i) + RADIUS;
    const x2 = cxFor(i + 1) - RADIUS;
    const mid = (x1 + x2) / 2;
    return `M ${x1} ${cy} C ${mid} ${cy + 6} ${mid} ${cy + 6} ${x2} ${cy}`;
  };

  // Label wrap on underscore — "cache_lookup" → ["cache", "lookup"], each
  // on its own tspan line. Keeps cell width small while staying readable.
  const labelLines = (label: string) =>
    label.includes("_") ? label.split("_") : [label];

  return (
    <div className="wf-hero-node llm wf-hero-node-multi">
      {connector && <div className="wf-hero-multi-tag">{connector}</div>}
      <svg
        viewBox={`0 0 ${totalW} ${CELL_H}`}
        preserveAspectRatio="xMidYMid meet"
        width="100%"
        height={CELL_H}
        role="img"
        aria-label="Pipeline graph"
        className="wf-hero-multi-svg"
      >
        {interior.slice(0, -1).map((_, i) => (
          <path key={`edge-${i}`} className="edge" d={edgePath(i)} />
        ))}
        {interior.map((n, i) => {
          const isSelected = selected === n.id;
          const isLlm = n.kind === "llm";
          const active = isLlm && isLive && BACKEND_ACTIVE_PHASES.has(phase ?? "");
          const model = active ? (liveModelFor(n.id) ?? "running") : null;
          const cxPos = cxFor(i);
          const dotCls = cx(
            "node",
            `kind-${n.kind || "tool"}`,
            isSelected && "selected",
            active && "active",
          );
          const parts = labelLines(n.label);
          return (
            <g
              key={n.id}
              className="wf-hero-multi-node"
              transform={`translate(${cxPos} 0)`}
              role="button"
              tabIndex={0}
              aria-pressed={isSelected}
              aria-label={n.label}
              onClick={() => setSelected(isSelected ? null : n.id)}
              onKeyDown={(e) => {
                if (e.key === "Enter" || e.key === " ") {
                  e.preventDefault();
                  setSelected(isSelected ? null : n.id);
                }
              }}
            >
              <circle className={dotCls} cx={0} cy={cy} r={RADIUS} />
              <text className="node-label" x={0} y={cy + 16} textAnchor="middle">
                {parts.map((p, j) => (
                  <tspan key={j} x={0} dy={j === 0 ? 0 : 11}>
                    {p}
                  </tspan>
                ))}
              </text>
              {model && (
                <text
                  className="node-sub"
                  x={0}
                  y={cy + 16 + parts.length * 11 + 2}
                  textAnchor="middle"
                >
                  {model}
                </text>
              )}
            </g>
          );
        })}
      </svg>
    </div>
  );
}

export function TargetPipelineHero({ samplesOpen, onToggle }: Props) {
  const cv = useConnector();
  const { dash } = useDashboard();
  const interior = cv.view ? interiorNodes(cv.view) : [];
  // The running searchpoint's resolved model when live; static origin otherwise.
  const liveModelFor = liveModelResolver(
    cv.isLive ? liveObserveConfig(dash) : null,
    cv.nodeConfigSchema,
  );

  return (
    <div className="wf-hero-flow">
      <button
        type="button"
        className="wf-hero-node wf-hero-node-toggle"
        aria-pressed={samplesOpen}
        aria-label={samplesOpen ? "Hide project preview" : "Show project preview"}
        onClick={onToggle}
      >
        <div className="ico">{ATTACH_ICON}</div>
        <div className="text-col">
          <div className="lbl">Input</div>
          <div className="val">Query</div>
        </div>
      </button>
      <div className="wf-hero-arrow">
        <ConnectorInspector view={cv} />
      </div>
      {cv.view == null || interior.length === 0 ? (
        // View not loaded (fetch in flight / failed / no dataset), or a degenerate
        // empty pipeline — honest placeholder, never a fabricated node.
        <PipelinePlaceholder />
      ) : interior.length === 1 ? (
        // A genuine single-node pipeline (first-class since the is_single_node
        // refactor) — render its REAL node, not a synthesized "LLM" stand-in.
        <SingleNodeChip
          node={interior[0]}
          liveModelFor={liveModelFor}
          phase={cv.phase}
          isLive={cv.isLive}
        />
      ) : (
        <MultiNodeStrip
          view={cv.view}
          connector={cv.connector}
          liveModelFor={liveModelFor}
          phase={cv.phase}
          isLive={cv.isLive}
        />
      )}
      <div className="wf-hero-arrow" />
      <button
        type="button"
        className="wf-hero-node wf-hero-node-toggle"
        aria-pressed={samplesOpen}
        aria-label={samplesOpen ? "Hide project preview" : "Show project preview"}
        onClick={onToggle}
      >
        <div className="ico">{ANSWER_ICON}</div>
        <div className="text-col">
          <div className="lbl">Output</div>
          <div className="val">Answer</div>
        </div>
      </button>
    </div>
  );
}
