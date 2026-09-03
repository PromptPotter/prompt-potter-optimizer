"use client";
import type { ReactNode } from "react";
import {
  nodeKind,
  nodeSubLabel,
  type PipelineView,
  type PipelineViewNode,
} from "@/components/workflow";
import type { NodeConfigParam } from "@/lib/api";
import type { NodeScope } from "@/lib/SelectionContext";
import type { PipelineStatus } from "@/lib/types";
import { useDashboard } from "@/lib/hooks/useDashboard";
import { useSelection } from "@/lib/SelectionContext";
import {
  agentLabel,
  cycleOf,
  interiorNodes,
  layoutGrid,
  liveObserveConfig,
  nodeReach,
} from "@/lib/derivations";
import { TERMS } from "@/lib/terms";
import { cx } from "@/lib/cx";

// One level of the stack: a UNIT that opens into another one. `PipelineStack` composes them
// and owns everything about the chain; this file draws a single level.

// The backend target LLM is called exactly while the OPTIMIZER's scoring node is active —
// `l1_score` covers origin scoring too and, unlike `dash.state`, does not flicker between
// samples. Optimizer-side only: compared against the served `active_node`, never against a
// target node's id (`nests.node` answers that).
const OPTIMIZER_SCORING_NODE = "l1_score";

// The served edge vocabulary, each with its own ink, dash and arrowhead in `chat.css`. An
// unrecognised kind draws as `forward` rather than unstyled — the wire's `kind` is a bare
// string, so a manifest can name one this does not know.
const EDGE_KINDS: readonly string[] = ["forward", "loop", "escalate", "directive"];

// How far a node's name and the model under it reach BELOW its dot — the baseline offset
// plus the second line's descender. Every label sits below its dot, so this is the only
// direction that needs clearing.
const LABEL_BELOW_EXTENT = 38;

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
  // The node that runs a nested pipeline, and what clicking it does. The FRAME is a fact
  // about the node — it runs another pipeline — so it draws wherever that node is named;
  // `onIsolate` is null where no level below is on screen (the dashboard's Optimizer card
  // draws one level and zooms nowhere), and the node then selects like any other.
  nest: { node: string; onIsolate: (() => void) | null } | null;
  // Drawn around another level ⇒ context, not subject: no labels, a third of the height.
  // From the flow, not from `nest`, which is null when the nesting node is unresolved.
  compact: boolean;
  // Per-node resolved model, to print under each name. Its PRESENCE is what widens the
  // cells to fit one — a provider-qualified model is far wider than a node id, and the
  // nested levels have no room for either, so they pass nothing and stay narrow.
  models: { by: Record<string, string | null>; loading: boolean } | null;
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
  models,
}: BoxProps) {
  const { node: selected, setSelectionForNode: setSelected } = useSelection();
  const { dash } = useDashboard();
  const interior = interiorNodes(view);
  // Compact drops the label row, which is most of the height; the names stay in `<title>`
  // and `aria-label`. A level printing models needs the pitch to fit one — a
  // provider-qualified name is far wider than the node id above it.
  const CELL_W = compact ? 44 : models ? 132 : 72;
  const CELL_W_OPEN = 132;
  // How narrow a sibling may be squashed. Not 0: a cell still has to show its dot and take
  // a tap, so when the floor binds the bonus shrinks instead of a cell being dropped.
  const CELL_W_MIN = 20;
  const ROW_H = compact ? 26 : 70;
  const RADIUS = compact ? 5.5 : 7;
  const cy = compact ? 13 : 14;
  // How far a node's TEXT reaches below its dot, and how much clear air a backward edge
  // has above its row. Compact draws no labels, so nothing has to be cleared.
  const LABEL_BLOCK = compact ? RADIUS : 34;
  const ROW_GAP = compact ? 18 : 38;
  // The clickable band around one dot on the grid, where nodes stack in both axes and a
  // full-height rect each would leave only the last-drawn one reachable.
  const HIT_BAND = compact ? 24 : 40;
  const isSel = (id: string) => scope != null && selected?.scope === scope && selected.id === id;
  // A nesting node isolates rather than selects: its knobs live in another cycle. Returns
  // the nest so a caller reading it gets narrowing.
  const nestAt = (id: string) => (nest != null && id === nest.node ? nest : null);
  const activate = (id: string) => {
    const here = nestAt(id);
    if (here?.onIsolate) return here.onIsolate();
    if (scope == null) return;
    setSelected(isSel(id) ? null : { id, scope });
  };

  // Read off the SERVED graph: the `loop` edge names where the repeat closes, and the
  // forward chain between its ends is the cycle. A chain declares no loop and gets none.
  const cycle = cycleOf(interior, view.edges);

  // A loopless view is a straight rail, and the producer guarantees every node on one is
  // tier 0 (`derive_pipeline_view`: an escalation always closes a loop), so `rank` alone
  // columns it.
  const cols = Math.max(interior.length, 1);

  // Per-cell widths, then cumulative offsets. The open cell's bonus is capped by what the
  // siblings can give above CELL_W_MIN and they give exactly it, so the total stays
  // `cols * CELL_W` and expanding never pushes the tail out of the frame. The bonus buys
  // room for an unwrapped label, so a compact level has none.
  const others = Math.max(cols - 1, 0);
  const givable = others * (CELL_W - CELL_W_MIN);
  const selectedCol = interior.find((n) => isSel(n.id))?.rank ?? -1;
  const bonus =
    !compact && selectedCol >= 0 ? Math.min(CELL_W_OPEN - CELL_W, givable) : 0;
  const shrink = others > 0 ? bonus / others : 0;
  const widths = Array.from({ length: cols }, (_, i) =>
    i === selectedCol ? CELL_W + bonus : CELL_W - shrink,
  );
  const offsets: number[] = [];
  widths.reduce((acc, w, i) => {
    offsets[i] = acc;
    return acc + w;
  }, 0);
  const railW = cols * CELL_W;
  const colX = (i: number) => (offsets[i] ?? 0) + (widths[i] ?? CELL_W) / 2;

  // A graph that REPEATS folds onto a grid rather than running as a line with a wire
  // hanging off the end: on a rail the return has to cross the whole width under the
  // labels, and an escalation lands far from the step that reaches for it. A chain has no
  // cycle and keeps the rail. EVERY surface draws a looping pipeline the same way, compact
  // included — one that folds on the dashboard and runs flat in the hero is two pictures of
  // one graph. Compact keeps its own metrics: no labels, so the rows sit close.
  const ring = cycle.length
    ? layoutGrid(interior, cycle, {
        cell: CELL_W,
        // Clearance for a name AND the model under it. Two pixels short and the sublabel
        // is shaved off with nothing on screen to say so.
        rowH: compact ? 24 : LABEL_BELOW_EXTENT + 34,
        padTop: compact ? 13 : 16,
        padBottom: compact ? 13 : LABEL_BELOW_EXTENT,
      })
    : null;

  const totalW = ring ? ring.width : railW;
  const canvasH = ring ? ring.height : ROW_H;
  const at = (n: PipelineViewNode) =>
    ring?.pos.get(n.id) ?? { x: colX(n.rank), y: cy, muted: false };
  const posOf = new Map(interior.map((n) => [n.id, at(n)] as const));

  // One ribbon per SERVED edge, and only where one is served — never between
  // array-adjacent nodes, which is right for a straight dataset and a falsehood for any
  // pipeline that loops or escalates.
  const edgeD = (a: { x: number; y: number }, b: { x: number; y: number }) => {
    const dx = b.x - a.x;
    const dy = b.y - a.y;
    const len = Math.hypot(dx, dy) || 1;
    // Endpoints sit at the dot's edge, so a stroke never runs under the dot it leaves.
    const [x1, y1] = [a.x + (dx / len) * RADIUS, a.y + (dy / len) * RADIUS];
    const [x2, y2] = [b.x - (dx / len) * RADIUS, b.y - (dy / len) * RADIUS];
    const mx = (x1 + x2) / 2;

    // Every node's name and model sit DIRECTLY BELOW its dot; all three rules follow.
    // BETWEEN ROWS — the upper node is joined below its text, never at its dot, so the
    // stroke stops short of the block instead of running down through both lines of it.
    // The lower node has nothing above it and keeps its dot edge.
    if (Math.abs(dy) > 1) {
      const down = a.y < b.y;
      const [upper, lower] = down ? [a, b] : [b, a];
      const top = { x: upper.x, y: upper.y + LABEL_BLOCK };
      const bottom = { x: lower.x, y: lower.y - RADIUS };
      const [p0, p1] = down ? [top, bottom] : [bottom, top];
      return `M ${p0.x} ${p0.y} Q ${(p0.x + p1.x) / 2} ${(p0.y + p1.y) / 2} ${p1.x} ${p1.y}`;
    }
    // ALONG A ROW — a step forward keeps its soft sag, which clears the text below. A step
    // BACK bows the other way, up into the gap above: sagging it would pass straight over
    // the name and model of every node it spans. Longer spans rise further, so two
    // backward reaches from one node stay told apart.
    const rise = Math.min(len * 0.16, ROW_GAP * 0.78);
    const bow = dx > 0 ? 6 : -rise;
    return `M ${x1} ${y1} C ${mx} ${y1 + bow} ${mx} ${y2 + bow} ${x2} ${y2}`;
  };

  // An edge onto a node this box does not draw is a TERMINAL: the `io` ends, which
  // `interiorNodes` drops. It still has to appear — the produced searchpoint leaving the
  // loop is the whole point of the run, so dropping it would take the arrow off the last
  // node with nothing to say it had gone. Leaves rightward, from the dot's edge.
  const terminalD = (p: { x: number; y: number }) =>
    `M ${p.x + RADIUS} ${p.y} L ${p.x + CELL_W * 0.5} ${p.y}`;

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
          aria-pressed={soleNest?.onIsolate ? undefined : isSelected}
          aria-label={
            soleNest?.onIsolate
              ? `${sole.label} — show what it runs, alone`
              : `Node: ${sole.label}`
          }
          disabled={scope == null && soleNest?.onIsolate == null}
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
        viewBox={`0 0 ${totalW} ${canvasH}`}
        preserveAspectRatio="xMidYMid meet"
        width="100%"
        height={canvasH}
        style={{ minWidth: `${totalW}px` }}
        role="img"
        aria-label="Pipeline graph"
        className="wf-hero-multi-svg"
      >
        <defs>
          {EDGE_KINDS.map((k) => (
            <marker
              key={k}
              id={`wf-arrow-${k}`}
              markerWidth="7"
              markerHeight="7"
              refX="6"
              refY="3.5"
              orient="auto"
            >
              <path className={cx("wf-arrow", `kind-${k}`)} d="M0,0 L7,3.5 L0,7 z" />
            </marker>
          ))}
        </defs>
        {view.edges.map((e) => {
          const a = posOf.get(e.from);
          const b = posOf.get(e.to);
          if (!a && !b) return null;
          const kind = EDGE_KINDS.includes(e.kind) ? e.kind : "forward";
          // Only the OUTGOING end gets a stub. A level that shows where a sample enters
          // draws an Input chip for it; inside the box there is no room left of the first
          // node, and an arrow arriving there points backwards into it.
          if (!a) return null;
          const d = b ? edgeD(a, b) : terminalD(a);
          return (
            <path
              key={`${e.from}>${e.to}`}
              // An edge touching the receded preamble recedes with it — dimming the node
              // while its wire stayed full-strength just moved the eye onto the wire.
              className={cx("edge", `kind-${kind}`, (a.muted || b?.muted) && "muted")}
              d={d}
              markerEnd={`url(#wf-arrow-${kind})`}
            />
          );
        })}
        {interior.map((n) => {
          const isSelected = isSel(n.id);
          const isActive = isLive && activeNode === n.id;
          const { x: cxPos, y: nodeY, muted } = at(n);
          const dotCls = cx(
            "node",
            nodeKind(n.kind).cls,
            isSelected && "selected",
            isActive && "active",
          );
          // The glyph answers WHERE THE SEARCH REACHES, and the POSITIVE state is the one that
          // gets the mark: most nodes in a real pipeline declare no axis at all — tools,
          // measurement, plumbing — so marking that case makes the default the loudest thing
          // on screen and leaves the product's own behaviour unmarked.
          //
          //   FRAME    — runs a whole nested pipeline; what it IS, before any axis question
          //   RING     — some agent searches here. The optimizer's actual reach.
          //   PADLOCK  — nothing here is searched, though it COULD be: opening an axis is
          //              adding its key to `param_keys`, so a config param no agent moves is
          //              shut, not exempt. Closed shackle = every openable axis shut; open
          //              shackle = some shut, some searched.
          //   bare dot — nothing here could ever be opened (`model`/`provider` only, or no
          //              params at all), or the reading is unknown.
          //
          // Ring and padlock COMPOSE on a partial node: it is searched AND partly shut, and
          // those are two facts rather than a choice between two glyphs.
          const nests = nestAt(n.id);
          const reach = nodeReach(schema, n.id);
          // Drawn on a nesting node too: the badge sits BESIDE the glyph, so "this runs a
          // pipeline" and "its own axes are shut" never compete for one mark. `l1_score` is both.
          const lock: "open" | "closed" | null = !reach
            ? null
            : reach.state === "locked"
              ? "closed"
              : reach.state === "partial"
                ? "open"
                : null;
          const reached = reach != null && reach.open > 0;
          // Said in words too: the glyph carries three shapes and the operator's question is
          // "which params, and moved by whom", which only a count and a name can answer.
          const reachNote =
            reach == null || reach.state === "nothing"
              ? null
              : reach.open === 0
                ? `no axis open of ${reach.openable}${reach.held ? " — narrowed at mint" : ""}; open one by forking`
                : `${reach.open} of ${reach.openable} axes open — ${reach.agents.map(agentLabel).join(", ")}`;
          // Wrapped while narrow, whole id once widened — that is what expanding buys. A
          // grid never wraps: it is already pitched wide enough for the id and the model.
          const parts = isSelected || ring ? [n.label] : labelLines(n.label);
          const cellW = ring ? CELL_W : (widths[n.rank] ?? CELL_W);
          // A squashed sibling drops its label rather than spilling into its neighbours.
          const showLabel = !compact && cellW >= 44;
          const labelDy = 16;
          const sub = models
            ? nodeSubLabel(n.kind, models.by[n.id] ?? null, models.loading)
            : "";
          // The glossary line for this node, where the operator vocabulary has one — a
          // property of the node id, not of the surface drawing it.
          const tip = TERMS[`node_${n.id}`];
          const subDy = labelDy + parts.length * 11;
          const inert = scope == null && nests?.onIsolate == null;
          return (
            <g
              key={n.id}
              className={cx("wf-hero-multi-node", inert && "inert", muted && "muted")}
              transform={`translate(${cxPos} 0)`}
              role={inert ? undefined : "button"}
              tabIndex={inert ? undefined : 0}
              aria-pressed={inert || nests?.onIsolate ? undefined : isSelected}
              aria-label={
                nests?.onIsolate
                  ? `${n.label} — show what it runs, alone`
                  : reachNote
                    ? `${n.label} — ${reachNote}`
                    : n.label
              }
              onClick={inert ? undefined : () => activate(n.id)}
              onKeyDown={(e) => {
                if (inert) return;
                if (e.key === "Enter" || e.key === " ") {
                  e.preventDefault();
                  activate(n.id);
                }
              }}
            >
              {/* Hit target: the whole cell on a rail, where the gap between dot and label
                  is otherwise dead — but only this node's own band on a grid, where a
                  full-height rect each would leave the last-drawn one alone clickable. */}
              <rect
                x={-cellW / 2}
                y={ring ? nodeY - HIT_BAND / 2 : 0}
                width={cellW}
                height={ring ? HIT_BAND : ROW_H}
                fill="transparent"
              />
              <title>
                {[
                  n.label,
                  nests
                    ? nests.onIsolate
                      ? "runs the pipeline below; show it alone"
                      : "runs a whole pipeline of its own"
                    : tip,
                  reachNote,
                ]
                  .filter(Boolean)
                  .join(" — ")}
              </title>
              {/* The node's OWN glyph, never replaced — reach and locking are things TRUE OF a
                  node, so they adorn it. Swapping the dot for a padlock spends the kind
                  vocabulary and leaves "what is this node" unanswerable at a glance. */}
              {nests ? (
                <g
                  className={cx("node-nest", isActive && "active")}
                  transform={`translate(0 ${nodeY})`}
                >
                  <rect className="frame" x={-8} y={-6} width={16} height={12} rx={2.5} />
                  <rect className="inner" x={-4.5} y={-2.5} width={9} height={5} rx={1.5} />
                </g>
              ) : (
                <circle className={dotCls} cx={0} cy={nodeY} r={RADIUS} />
              )}
              {/* Halo where the search works. Wider than the dot, under nothing. */}
              {reached && (
                <circle
                  className={cx("node-reach", isActive && "active")}
                  cx={0}
                  cy={nodeY}
                  r={RADIUS + 3.5}
                />
              )}
              {/* Corner badge: axes nothing searches, though they could be opened. Dropped on a
                  compact level, which draws context rather than subject and has no room. */}
              {lock && !compact && (
                <g
                  className={cx("node-lock", `is-${lock}`)}
                  transform={`translate(${RADIUS + 3.4} ${nodeY - RADIUS - 1.5}) scale(0.78)`}
                >
                  {/* Open = the right leg never meets the body, the only shape difference
                      legible at this size. */}
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
              )}
              {showLabel && (
                <text className="node-label" x={0} y={nodeY + labelDy} textAnchor="middle">
                  {parts.map((p, j) => (
                    <tspan key={j} x={0} dy={j === 0 ? 0 : 11}>
                      {p}
                    </tspan>
                  ))}
                </text>
              )}
              {showLabel && sub && (
                <text className="node-sub" x={0} y={nodeY + subDy} textAnchor="middle">
                  {sub}
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
  // and React destroys the state of a re-parented component. Absent where a host draws one
  // level only — `nestsNode` still marks the nesting node, which is a fact about the node
  // rather than about how many levels this host happens to show.
  nest?: { level: ReactNode; onIsolate: () => void };
  // Half of the depth alternation, from the stack — a level cannot know its own depth and
  // CSS cannot count from the inside out.
  tone: "accent" | "neutral";
  // Per-node resolved model to print under each name; widens the cells to fit one. Only a
  // level with room passes it — see `BoxProps.models`.
  models?: { by: Record<string, string | null>; loading: boolean } | null;
  // Drawn without the surrounding flow chrome: no unit wrapper, no tone band. For a host
  // that is already a card — the Optimizer card owns its own frame and toolbar.
  bare?: boolean;
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
  models = null,
  bare = false,
}: PipelineFlowProps) {
  const interior = interiorNodes(view);
  const box =
    view == null || interior.length === 0 ? (
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
        // The frame follows the served id; only the ZOOM follows the level being drawn.
        nest={nestsNode ? { node: nestsNode, onIsolate: nest?.onIsolate ?? null } : null}
        compact={nest != null}
        models={models}
      />
    );

  if (bare) return box;

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
        {box}
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
