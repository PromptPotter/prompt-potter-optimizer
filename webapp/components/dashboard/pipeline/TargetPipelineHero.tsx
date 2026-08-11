"use client";
import type { PipelineView, PipelineViewNode } from "@/components/workflow";
import type { NodeConfigParam } from "@/lib/api";
import type { PipelineStatus } from "@/lib/types";
import { ConnectorInspector } from "./ConnectorInspector";
import { useConnector } from "@/lib/hooks/useConnector";
import { useDashboard } from "@/lib/hooks/useDashboard";
import { useSelection } from "@/lib/SelectionContext";
import { isSelfOptimization, liveObserveConfig } from "@/lib/derivations";
import { cx } from "@/lib/cx";

// The backend target LLM is being called exactly while the OPTIMIZER's scoring node is active.
// `l1_score` covers origin scoring too and does not flicker between samples the way `dash.state`
// does (scoring → between_samples → scoring per sample, which forced a four-member phase set
// here). During the optimizer phases that node is inactive and the backend sits idle.
const BACKEND_SCORING_NODE = "l1_score";

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

// No pipeline view to draw. NOT a real node: we must not fabricate a single "LLM"
// chip here (it would misrepresent a failed/loading fetch — or a real 5-node
// pipeline — as a genuine single-LLM pipeline). Neutral, non-clickable, no model.
//
// The three reasons `view` can be missing are DIFFERENT and must read differently.
// This once collapsed them into one "—" with an unconditional `aria-busy`, so a
// read that had already failed announced itself as loading, forever — the operator
// had no way to tell a slow pipeline from a broken one, and the answer to "why is
// there a dash?" was only findable in the network tab.
function PipelinePlaceholder({ status }: { status: PipelineStatus }) {
  const [label, value, hint] =
    status === "error"
      ? (["Pipeline", "unavailable", "Couldn't read this campaign's dataset."] as const)
      : status === "loading"
        ? (["Pipeline", "loading…", undefined] as const)
        : (["Pipeline", "none", "No dataset bound to this campaign."] as const);
  return (
    <div
      className="wf-hero-node"
      aria-label={`Pipeline ${value}`}
      aria-busy={status === "loading" || undefined}
      title={hint}
    >
      <div className="head">
        <div className="ico">{LLM_ICON}</div>
        <div className="lbl">{label}</div>
      </div>
      <div className="val">{value}</div>
    </div>
  );
}

// THE box: a glassmorphic frame tagged with what it is, holding the nodes inside
// it. One box at every size — a second component for the single-node case drew
// the same frame and hardcoded the label "LLM" over the node's real name. Labels
// wrap on underscores (one `<tspan>` per part) to keep cell width compact for
// ids like `cache_lookup`.
function PipelineBox({
  view,
  connector,
  activeNode,
  isLive,
  schema,
  selfOpt,
}: {
  view: PipelineView;
  connector: string | null;
  activeNode: string | null;
  isLive: boolean;
  schema: Record<string, NodeConfigParam[]> | null;
  selfOpt: boolean;
}) {
  const { node: selected, setSelectionForNode: setSelected } = useSelection();
  const { dash } = useDashboard();
  const interior = interiorNodes(view);
  const CELL_W = 72;
  const CELL_W_OPEN = 132;
  // How narrow a sibling may be squashed. Not 0: a cell still has to show its
  // dot and take a tap, so when the floor binds the bonus shrinks instead — a
  // long pipeline expands less, rather than losing cells.
  const CELL_W_MIN = 20;
  const CELL_H = 70;
  const RADIUS = 7;
  const cy = 14;
  const isSel = (id: string) => selected?.scope === "target" && selected.id === id;

  // Per-cell widths, then cumulative offsets. The bonus the open cell takes is
  // capped by what the siblings can actually give without falling under
  // CELL_W_MIN, and they give exactly it — so the total is invariant at
  // `length * CELL_W` and expanding never pushes the tail out of the frame.
  const others = Math.max(interior.length - 1, 0);
  const givable = others * (CELL_W - CELL_W_MIN);
  const bonus = interior.some((n) => isSel(n.id))
    ? Math.min(CELL_W_OPEN - CELL_W, givable)
    : 0;
  const shrink = others > 0 ? bonus / others : 0;
  const widths = interior.map((n) => (isSel(n.id) ? CELL_W + bonus : CELL_W - shrink));
  const offsets: number[] = [];
  widths.reduce((acc, w, i) => {
    offsets[i] = acc;
    return acc + w;
  }, 0);
  const totalW = interior.length * CELL_W;
  const cxFor = (i: number) => (offsets[i] ?? 0) + (widths[i] ?? CELL_W) / 2;

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

  // Same rule the optimizer canvas uses: light the node whose id IS the served
  // `active_node`. That resolves for a self-optimizing campaign, whose target
  // pipeline IS the optimizer's own nodes; for any other backend it names
  // nothing, which is the truth — `active_node` speaks for the optimizer, and
  // which BACKEND node is mid-call is not served at all. So the whole-chip pulse
  // is the fallback for exactly that case, never a second signal beside it.
  const namedHere = isLive && interior.some((n) => n.id === activeNode);
  const calling = isLive && activeNode === BACKEND_SCORING_NODE && !namedHere;

  // ONE node is not a graph. The box's tag already names the pipeline, so a strip
  // of one dot draws a shape that isn't there — show the node itself, and the
  // model it resolves to, which is the only fact a single-node backend has to
  // give. Same box, same selection axis; just no graph inside it.
  const sole = interior.length === 1 ? interior[0] : undefined;
  // The in-flight candidate's server-resolved model, falling back to the dataset
  // overlay's static one. `current_round.nodes` carries only OPTIMIZER calls,
  // never the backend target, so the running model comes off the searchpoint.
  const liveCfg = sole ? liveObserveConfig(dash)?.config[sole.id] : null;
  const liveModel =
    liveCfg && typeof liveCfg === "object"
      ? (liveCfg as Record<string, unknown>).model
      : null;
  const staticModel = sole ? schema?.[sole.id]?.find((p) => p.key === "model")?.value : null;
  const soleModel =
    typeof liveModel === "string" && liveModel
      ? liveModel
      : typeof staticModel === "string" && staticModel
        ? staticModel
        : null;
  if (sole) {
    const isSelected = isSel(sole.id);
    return (
      <button
        type="button"
        className={cx("wf-hero-node", "llm", isSelected && "selected", calling && "active")}
        aria-pressed={isSelected}
        aria-label={`Node: ${sole.label}`}
        onClick={() => setSelected(isSelected ? null : { id: sole.id, scope: "target" })}
      >
        {connector && <div className="wf-hero-multi-tag">{connector}</div>}
        <div className="head">
          <div className="ico">{LLM_ICON}</div>
          <div className="lbl">{sole.label}</div>
        </div>
        <div className="val">{calling ? (soleModel ?? "running") : "idle"}</div>
      </button>
    );
  }

  return (
    <div className={cx("wf-hero-node", "llm", "wf-hero-node-multi", calling && "active")}>
      {connector && <div className="wf-hero-multi-tag">{connector}</div>}
      <div className="wf-hero-multi-rail">
      {/* width:100% so the whole pipeline SCALES into the frame. The min-width is
          the floor where that stops being honest: below ~44px a cell is neither
          readable nor tappable, so past that many nodes the rail scrolls. */}
      <svg
        viewBox={`0 0 ${totalW} ${CELL_H}`}
        preserveAspectRatio="xMidYMid meet"
        width="100%"
        height={CELL_H}
        style={{ minWidth: `${interior.length * 44}px` }}
        role="img"
        aria-label="Pipeline graph"
        className="wf-hero-multi-svg"
      >
        {interior.slice(0, -1).map((_, i) => (
          <path key={`edge-${i}`} className="edge" d={edgePath(i)} />
        ))}
        {interior.map((n, i) => {
          const isSelected = selected?.scope === "target" && selected.id === n.id;
          const isActive = isLive && activeNode === n.id;
          const cxPos = cxFor(i);
          const dotCls = cx(
            "node",
            `kind-${n.kind || "tool"}`,
            isSelected && "selected",
            isActive && "active",
          );
          // Three shapes, distinguished by what the optimizer vs the operator may do:
          //   dot         — the optimizer moves it (some param is tunable)
          //   OPEN lock   — the optimizer will not, but the operator can; doing it
          //                 on a live cycle stamps `human_intervened` (grade C)
          //   CLOSED lock — nothing to change; the node carries no params at all
          // A null schema is UNKNOWN (demo / not yet loaded) and keeps the dot —
          // it must not read as locked.
          // Fourth shape, and it exists because `params.length === 0` conflated two
          // facts. On a self-optimizing campaign the scoring node RUNS the backend —
          // a whole inner pipeline, configured in `inner_tasks.yaml` and absent from
          // this wire — so it is neither tunable here nor genuinely paramless. A
          // closed padlock said "nothing to change" about the one node that holds
          // everything. It draws as a FRAME instead and claims no lock either way.
          const nested = selfOpt && n.id === BACKEND_SCORING_NODE;
          const params = schema?.[n.id];
          const tunable = params != null && params.some((p) => p.optimizer_tunable);
          const paramless = params != null && params.length === 0;
          const lock: "open" | "closed" | null =
            nested || params == null || tunable ? null : paramless ? "closed" : "open";
          // Wrapped short form while narrow; the widened cell has room for the
          // whole id on one line, which is the "more info" the expansion buys.
          const parts = isSelected ? [n.label] : labelLines(n.label);
          const cellW = widths[i] ?? CELL_W;
          // A squashed sibling drops its label rather than letting centred text
          // spill into its neighbours. The dot still marks the node, `<title>` and
          // aria-label still name it, and opening it brings the label back.
          const showLabel = cellW >= 44;
          return (
            <g
              key={n.id}
              className="wf-hero-multi-node"
              transform={`translate(${cxPos} 0)`}
              role="button"
              tabIndex={0}
              aria-pressed={isSelected}
              aria-label={n.label}
              onClick={() => setSelected(isSelected ? null : { id: n.id, scope: "target" })}
              onKeyDown={(e) => {
                if (e.key === "Enter" || e.key === " ") {
                  e.preventDefault();
                  setSelected(isSelected ? null : { id: n.id, scope: "target" });
                }
              }}
            >
              {/* Full-cell hit target — the dot is r=7 with the label below it, so
                  the gap between them is otherwise dead to a finger. Same reason
                  WorkflowCanvas backs its nodes with a transparent rect. */}
              <rect x={-cellW / 2} y={0} width={cellW} height={CELL_H} fill="transparent" />
              <title>
                {nested ? `${n.label} — runs the inner backend pipeline` : n.label}
              </title>
              {nested ? (
                <g
                  className={cx("node-nest", isSelected && "selected", isActive && "active")}
                  transform={`translate(0 ${cy})`}
                >
                  <rect className="frame" x={-8} y={-6} width={16} height={12} rx={2.5} />
                  <rect className="inner" x={-4.5} y={-2.5} width={9} height={5} rx={1.5} />
                </g>
              ) : lock ? (
                <g
                  className={cx(
                    "node-lock",
                    `is-${lock}`,
                    isSelected && "selected",
                    isActive && "active",
                  )}
                  transform={`translate(0 ${cy})`}
                >
                  {/* Open = the right leg never meets the body, which is the only
                      shape difference legible at this size. */}
                  <path
                    className="shackle"
                    d={
                      lock === "closed"
                        ? "M-2.6,-1.6 v-2.2 a2.6,2.6 0 0 1 5.2,0 v2.2"
                        : "M-2.6,-1.6 v-2.2 a2.6,2.6 0 0 1 5.2,0"
                    }
                  />
                  <rect className="body" x={-4.6} y={-1.6} width={9.2} height={7.4} rx={1.4} />
                </g>
              ) : (
                <circle className={dotCls} cx={0} cy={cy} r={RADIUS} />
              )}
              {showLabel && (
                <text className="node-label" x={0} y={cy + 16} textAnchor="middle">
                  {parts.map((p, j) => (
                    <tspan key={j} x={0} dy={j === 0 ? 0 : 11}>
                      {p}
                    </tspan>
                  ))}
                </text>
              )}
            </g>
          );
        })}
      </svg>
      </div>
    </div>
  );
}

export function TargetPipelineHero({ samplesOpen, onToggle }: Props) {
  const cv = useConnector();
  const { dash } = useDashboard();
  const activeNode = dash?.current_round.active_node ?? null;
  const interior = cv.view ? interiorNodes(cv.view) : [];

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
        // View not loaded (in flight / failed / no dataset bound) or a degenerate
        // empty pipeline — honest placeholder, never a fabricated node. An `ok`
        // read that still yields no interior nodes is a real empty pipeline, which
        // reads as "none" — the same thing an unbound campaign has.
        <PipelinePlaceholder status={cv.pipelineStatus === "ok" ? "unbound" : cv.pipelineStatus} />
      ) : (
        // One box at every size. A single-node pipeline is a strip of one — it
        // used to get its own chip that hardcoded the label "LLM", which was a
        // second surface saying a less true thing (the node has a real name).
        <PipelineBox
          view={cv.view}
          connector={cv.connector}
          activeNode={activeNode}
          isLive={cv.isLive}
          schema={cv.nodeConfigSchema}
          selfOpt={isSelfOptimization(cv.backendType)}
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
