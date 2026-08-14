"use client";
import type { ReactNode } from "react";
import type { PipelineView } from "@/components/workflow";
import type { NodeConfigParam } from "@/lib/api";
import type { NodeScope } from "@/lib/SelectionContext";
import type { PipelineStatus } from "@/lib/types";
import { useDashboard } from "@/lib/hooks/useDashboard";
import { useSelection } from "@/lib/SelectionContext";
import { interiorNodes, liveObserveConfig } from "@/lib/derivations";
import { cx } from "@/lib/cx";

// One level of the stack: a UNIT that opens into another one. `PipelineStack` composes them
// and owns everything about the chain; this file draws a single level.

// The backend target LLM is called exactly while the OPTIMIZER's scoring node is active —
// `l1_score` covers origin scoring too and, unlike `dash.state`, does not flicker between
// samples. Optimizer-side only: compared against the served `active_node`, never against a
// target node's id (`nests.node` answers that).
const OPTIMIZER_SCORING_NODE = "l1_score";

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

// No view to draw. Never fabricate a node here — that would show a failed read as a real
// single-LLM pipeline. The three reasons `view` can be missing must read differently: a
// slow pipeline and a broken one are not otherwise distinguishable on screen.
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

interface BoxProps {
  view: PipelineView;
  connector: string | null;
  activeNode: string | null;
  isLive: boolean;
  schema: Record<string, NodeConfigParam[]> | null;
  // Which namespace a node click writes. Null makes every node inert — a level whose
  // detail panel does not exist must not offer a click that opens nothing.
  scope: NodeScope | null;
  // The node that runs a nested pipeline, and what clicking it does. Null unless that
  // level is on screen, so the frame glyph means "there IS a level below" by construction.
  nest: { node: string; onIsolate: () => void } | null;
  // Drawn around another level ⇒ context, not subject: no labels, a third of the height.
  // From the flow, not from `nest`, which is null when the nesting node is unresolved.
  compact: boolean;
}

// THE box: a glassmorphic frame tagged with what it is, holding its nodes. One box at
// every size, single-node included.
function PipelineBox({
  view,
  connector,
  activeNode,
  isLive,
  schema,
  scope,
  nest,
  compact,
}: BoxProps) {
  const { node: selected, setSelectionForNode: setSelected } = useSelection();
  const { dash } = useDashboard();
  const interior = interiorNodes(view);
  // Compact drops the label row, which is most of the height; the names stay in `<title>`
  // and `aria-label`.
  const CELL_W = compact ? 44 : 72;
  const CELL_W_OPEN = 132;
  // How narrow a sibling may be squashed. Not 0: a cell still has to show its dot and take
  // a tap, so when the floor binds the bonus shrinks instead of a cell being dropped.
  const CELL_W_MIN = 20;
  const CELL_H = compact ? 26 : 70;
  const RADIUS = compact ? 5.5 : 7;
  const cy = compact ? 13 : 14;
  const isSel = (id: string) => scope != null && selected?.scope === scope && selected.id === id;
  // A nesting node isolates rather than selects: its knobs live in another cycle. Returns
  // the nest so a caller reading it gets narrowing.
  const nestAt = (id: string) => (nest != null && id === nest.node ? nest : null);
  const activate = (id: string) => {
    const here = nestAt(id);
    if (here) return here.onIsolate();
    if (scope == null) return;
    setSelected(isSel(id) ? null : { id, scope });
  };

  // Per-cell widths, then cumulative offsets. The open cell's bonus is capped by what the
  // siblings can give above CELL_W_MIN and they give exactly it, so the total stays
  // `length * CELL_W` and expanding never pushes the tail out of the frame. The bonus buys
  // room for an unwrapped label, so a compact level has none.
  const others = Math.max(interior.length - 1, 0);
  const givable = others * (CELL_W - CELL_W_MIN);
  const bonus =
    !compact && interior.some((n) => isSel(n.id))
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

  const labelLines = (label: string) =>
    label.includes("_") ? label.split("_") : [label];

  // Light the node whose id IS the served `active_node` — which resolves only on a
  // self-optimizing campaign, since `active_node` speaks for the optimizer and no wire
  // says which BACKEND node is mid-call. The whole-chip pulse covers that case instead,
  // never both at once.
  const namedHere = isLive && interior.some((n) => n.id === activeNode);
  const calling = isLive && activeNode === OPTIMIZER_SCORING_NODE && !namedHere;

  // One node is not a graph: draw the node and its resolved model, no strip.
  const sole = interior.length === 1 ? interior[0] : undefined;
  // Off the searchpoint, not `current_round.nodes`, which carries optimizer calls only.
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
    const soleNest = nestAt(sole.id);
    return (
      <div className={cx("wf-hero-node", "llm", isSelected && "selected", calling && "active")}>
        {connector && <div className="wf-hero-multi-tag">{connector}</div>}
        <button
          type="button"
          className="wf-hero-sole"
          aria-pressed={soleNest ? undefined : isSelected}
          aria-label={
            soleNest ? `${sole.label} — show what it runs, alone` : `Node: ${sole.label}`
          }
          disabled={scope == null && soleNest == null}
          onClick={() => activate(sole.id)}
        >
          <div className="head">
            <div className="ico">{LLM_ICON}</div>
            <div className="lbl">{sole.label}</div>
          </div>
          <div className="val">{calling ? (soleModel ?? "running") : "idle"}</div>
        </button>
      </div>
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
          const isSelected = isSel(n.id);
          const isActive = isLive && activeNode === n.id;
          const cxPos = cxFor(i);
          const dotCls = cx(
            "node",
            `kind-${n.kind || "tool"}`,
            isSelected && "selected",
            isActive && "active",
          );
          // Four shapes, by what the optimizer vs the operator may move:
          //   dot         — the optimizer moves it (some param is tunable)
          //   OPEN lock   — only the operator may, and on a live cycle that stamps
          //                 `human_intervened` (grade C)
          //   CLOSED lock — no params at all, nothing to change
          //   FRAME       — runs a whole nested pipeline, so neither tunable here nor
          //                 paramless; claims no lock either way
          // A null schema is UNKNOWN (demo / not yet loaded) and keeps the dot.
          const nests = nestAt(n.id);
          const params = schema?.[n.id];
          const tunable = params != null && params.some((p) => p.optimizer_tunable);
          const paramless = params != null && params.length === 0;
          const lock: "open" | "closed" | null =
            nests || params == null || tunable ? null : paramless ? "closed" : "open";
          // Wrapped while narrow, whole id once widened — that is what expanding buys.
          const parts = isSelected ? [n.label] : labelLines(n.label);
          const cellW = widths[i] ?? CELL_W;
          // A squashed sibling drops its label rather than spilling into its neighbours.
          const showLabel = !compact && cellW >= 44;
          const inert = scope == null && nests == null;
          return (
            <g
              key={n.id}
              className={cx("wf-hero-multi-node", inert && "inert")}
              transform={`translate(${cxPos} 0)`}
              role={inert ? undefined : "button"}
              tabIndex={inert ? undefined : 0}
              aria-pressed={inert || nests ? undefined : isSelected}
              aria-label={nests ? `${n.label} — show what it runs, alone` : n.label}
              onClick={inert ? undefined : () => activate(n.id)}
              onKeyDown={(e) => {
                if (inert) return;
                if (e.key === "Enter" || e.key === " ") {
                  e.preventDefault();
                  activate(n.id);
                }
              }}
            >
              {/* Full-cell hit target; the gap between dot and label is otherwise dead. */}
              <rect x={-cellW / 2} y={0} width={cellW} height={CELL_H} fill="transparent" />
              <title>
                {nests ? `${n.label} — runs the pipeline below; show it alone` : n.label}
              </title>
              {nests ? (
                <g
                  className={cx("node-nest", isActive && "active")}
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

export interface PipelineFlowProps {
  view: PipelineView | null;
  status: PipelineStatus;
  connector: string | null;
  schema: Record<string, NodeConfigParam[]> | null;
  scope: NodeScope | null;
  nestsNode: string | null;
  activeNode: string | null;
  isLive: boolean;
  // Rendered on this level's row, ahead of the Input end. The stack puts its zoom strip
  // here so the control shares a row with the ends instead of taking one of its own.
  leading?: ReactNode;
  // The Input/Output ends and the connector dot between them. Present on exactly one level
  // of a stack — the only one a sample flows through.
  queryPath?: {
    pressed: boolean;
    label: string;
    onClick: () => void;
    connector: ReactNode;
  };
  // The level this pipeline runs and what its nesting node does when clicked. Which levels
  // are drawn is owned by the STACK, never a `useState` here: a zoom re-parents this flow,
  // and React destroys the state of a re-parented component.
  nest?: { level: ReactNode; onIsolate: () => void };
  // Half of the depth alternation, from the stack — a level cannot know its own depth and
  // CSS cannot count from the inside out.
  tone: "accent" | "neutral";
}

// Module level, not a closure inside `PipelineFlow`: a component defined during render is
// remounted every pass, so state and focus inside it die on each poll tick
// (`react-hooks/static-components`).
function FlowEnd({
  icon,
  lbl,
  val,
  path,
}: {
  icon: ReactNode;
  lbl: string;
  val: string;
  path: NonNullable<PipelineFlowProps["queryPath"]>;
}) {
  return (
    <button
      type="button"
      className="wf-hero-node wf-hero-node-toggle"
      aria-pressed={path.pressed}
      aria-label={path.label}
      onClick={path.onClick}
    >
      <div className="ico">{icon}</div>
      <div className="text-col">
        <div className="lbl">{lbl}</div>
        <div className="val">{val}</div>
      </div>
    </button>
  );
}

export function PipelineFlow({
  view,
  status,
  connector,
  schema,
  scope,
  nestsNode,
  activeNode,
  isLive,
  leading,
  queryPath,
  nest,
  tone,
}: PipelineFlowProps) {
  const interior = interiorNodes(view);

  return (
    <div className="wf-hero-flow">
      {leading}
      {queryPath && (
        <>
          <FlowEnd icon={ATTACH_ICON} lbl="Input" val="Query" path={queryPath} />
          <div className="wf-hero-arrow">{queryPath.connector}</div>
        </>
      )}
      {/* The UNIT: the box and the level it runs as SIBLINGS, never nested — inside the
          box they fall under every `.wf-hero-node.llm <part>` rule in chat.css. The
          wrapper draws the containment. */}
      <div className={cx("wf-hero-unit", `tone-${tone}`, nest && "has-nested")}>
        {view == null || interior.length === 0 ? (
          // Not loaded, failed, unbound, or genuinely empty — never a fabricated node.
          <PipelinePlaceholder status={status === "ok" ? "unbound" : status} />
        ) : (
          <PipelineBox
            view={view}
            connector={connector}
            activeNode={activeNode}
            isLive={isLive}
            schema={schema}
            scope={scope}
            // Joining the served id to the level here keeps "the frame glyph means there
            // IS a level below" true by construction.
            nest={nest && nestsNode ? { node: nestsNode, onIsolate: nest.onIsolate } : null}
            compact={nest != null}
          />
        )}
        {nest && <div className="wf-hero-nested">{nest.level}</div>}
      </div>
      {queryPath && (
        <>
          <div className="wf-hero-arrow" />
          <FlowEnd icon={ANSWER_ICON} lbl="Output" val="Answer" path={queryPath} />
        </>
      )}
    </div>
  );
}
