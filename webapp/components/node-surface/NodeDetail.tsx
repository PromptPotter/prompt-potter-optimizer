import { useMemo } from "react";
import {
  type DraftPatch,
  type NodePrompt,
  type OptimizerNodePrompt,
  type NodeBlock,
  NODE_KIND_INFO,
  type PipelineViewNode,
} from "@/lib/api/types";
import type { ObserveState, SelectedNode } from "@/lib/address";
import { useSelection } from "@/lib/state/selection";
import { useCheckinDraft, useIngest } from "@/lib/state/ingest-flow";
import { draftPrompts, interiorNodes, type ObserveSubject } from "@/lib/derivations";
import { useWorkspace } from "@/lib/state/workspace";
import { cx } from "@/lib/cx";
import { useConnector } from "@/lib/hooks/useConnector";
import { readGap, shownData } from "@/lib/hooks/useRead";
import { useViewedDashboard } from "@/lib/state/poll";
import { useObserveSubject } from "@/lib/hooks/useObserveSubject";
import { useSearchpointRead, type SearchpointRead } from "@/lib/hooks/useSearchpointRead";
import { useRoundNodes } from "@/lib/hooks/useRoundNodes";
import { fmtSecs, fmtUnit, fmtValue, PLACEHOLDER } from "@/lib/format";
import { CopyButton, Empty, FailureLine, Loading, SegmentedControl, Term } from "@/components/ui";
import { NodeSurface } from "./NodeSurface";
import { MeasurementRun } from "./MeasurementRun";
import { L1Variants, variantsOf } from "./L1Variants";

interface NodeAuthoring {
  overlay: Record<string, unknown>;
  prompts: Record<string, NodePrompt>;
}

// The optimizer's own prompt, as the one prompt node it is.
function startPrompts(id: string, origin: OptimizerNodePrompt | null): Record<string, NodePrompt> {
  return origin ? { [id]: { fields: origin.fields, shots: [] } } : {};
}

// The `node` view's pane; under a check-in it authors the draft, under a run it reads what ran.
export function NodeDetail({ node: selected }: { node: SelectedNode }) {
  const { id, scope } = selected;
  const isOptimizer = scope === "optimizer";
  const { close } = useWorkspace();
  const flow = useIngest();
  const draft = useCheckinDraft();
  const onPromptApply = draft ? flow.applyPatch : undefined;
  const read = useConnector();
  const served = shownData(read);
  // A check-in's draft is the connector read's transport, so its params are the read's.
  const authoring = useMemo<NodeAuthoring | undefined>(
    () =>
      draft && !isOptimizer
        ? { overlay: served?.params ?? {}, prompts: draftPrompts(draft.origin_prompts) }
        : undefined,
    [draft, isOptimizer, served],
  );
  const { dash, isLive } = useViewedDashboard();
  const optimizer = served?.optimizer_setup ?? null;
  const subject = useObserveSubject();
  const observed = useSearchpointRead(!isOptimizer && !authoring ? subject.point : null, id);

  const view = isOptimizer ? optimizer?.view : served?.view;
  const node = interiorNodes(view).find((n) => n.id === id) ?? null;
  const servedKind = node?.kind ?? null;
  const modelCapabilities = isOptimizer
    ? (optimizer?.model_capabilities ?? {})
    : (served?.model_capabilities ?? {});

  const {
    nodes: roundNodes,
    round: viewedRound,
    showsCurrent: viewingLive,
    audit,
  } = useRoundNodes();
  const block: NodeBlock | null = isOptimizer ? (roundNodes[id] ?? null) : null;

  // Under the viewed config, its field edits laid on: what this node runs, not the bare template.
  const origin = (isOptimizer ? optimizer?.prompts[id] : undefined) ?? null;

  const identity = { id, scope, kind: servedKind };
  const program = isOptimizer
    ? { ...identity, prompts: startPrompts(id, origin) }
    : authoring
      ? {
          ...identity,
          resolved_params: authoring.overlay,
          prompts: authoring.prompts,
        }
      : observed.cfg
        ? {
            ...identity,
            searchpoint: observed.cfg.label,
            resolved_params: observed.cfg.config,
            prompts: observed.cfg.prompts,
          }
        : identity;
  const copyChoices = [
    { key: "program", label: "What this node runs", data: program },
    ...(block ? [{ key: "run", label: `What it did in round ${viewedRound}`, data: block }] : []),
  ];

  const livePhaseNode = dash?.current_round.active_node ?? null;
  const isLiveNow = isLive && viewingLive && livePhaseNode === id && isOptimizer;

  return (
    <div className={cx("bnode", "box-full", isOptimizer && "bnode-wide")}>
      <section className="setup-preview">
        <header className="setup-preview-head">
          <span className="setup-preview-title">
            {servedKind && (
              <span className={cx("bnode-kind", `kind-${servedKind}`)}>
                {NODE_KIND_INFO[servedKind].label}
              </span>
            )}
            <code className="opt-detail-id">{id}</code>
          </span>
          {/* A div: `CopyButton`'s choices menu opens a `Popover`, which is flow content. */}
          <div className="setup-preview-side">
            {/* The round axis sets the round; the dot lights while this node is the one firing. */}
            <span className={cx("opt-detail-status", isLiveNow && "live")}>
              ●{" "}
              {isOptimizer && viewedRound != null
                ? `round ${viewedRound}${viewingLive ? " · live" : ""}`
                : scopeLabel(isOptimizer)}
            </span>
            <CopyButton choices={copyChoices} title="Copy this node as JSON" />
            <button
              type="button"
              className="bnode-close"
              onClick={() => close("node")}
              aria-label="Close detail"
              title="Close"
            >
              ×
            </button>
          </div>
        </header>

        {node && <p className="bnode-role">{roleLine(node)}</p>}

        {isOptimizer ? (
          <OptimizerProgram
            id={id}
            node={node}
            origin={origin}
            modelCapabilities={modelCapabilities}
            onPromptApply={onPromptApply}
          />
        ) : (
          <TargetProgram
            node={node}
            authoring={authoring}
            subject={subject}
            observed={observed}
            modelCapabilities={modelCapabilities}
            isLive={isLive}
            onPromptApply={onPromptApply}
          />
        )}

        {/* `viewedRound == null` is no campaign (setup), not "did not fire this round". */}
        {isOptimizer && viewedRound != null && (
          <RunSection
            kind={servedKind}
            block={block}
            round={viewedRound}
            loading={audit.status === "loading"}
            kindLoading={readGap(read)?.status === "loading"}
            inFlight={isLiveNow}
          />
        )}
      </section>
    </div>
  );
}

function scopeLabel(isOptimizer: boolean): string {
  return isOptimizer ? "the optimizer's own loop" : "this campaign's pipeline";
}

// What the manifest says this node does, else what its kind is.
function roleLine(node: PipelineViewNode): string {
  return node.description || NODE_KIND_INFO[node.kind].role;
}

function OptimizerProgram({
  id,
  node,
  origin,
  modelCapabilities,
  onPromptApply,
}: {
  id: string;
  node: Parameters<typeof NodeSurface>[0]["node"];
  origin: OptimizerNodePrompt | null;
  modelCapabilities: Parameters<typeof NodeSurface>[0]["modelCapabilities"];
  // A check-in's: its optimizer prompt is edited here, as `nodes.<node>.config.<field>`.
  onPromptApply: ((patch: DraftPatch) => void) | undefined;
}) {
  return (
    <>
      <NodeSurface
        node={node}
        point={{ origin_prompts: startPrompts(id, origin), pipeline_overlay: {} }}
        overlay={{}}
        modelCapabilities={modelCapabilities}
        mode="values"
        promptScope="optimizer"
        edited={origin?.edited}
        onApply={onPromptApply}
      />
      {origin && origin.versions_declared > 1 && (
        <p className="inspector-note">
          Showing prompt {origin.version} of {origin.versions_declared} this node declares.
        </p>
      )}
    </>
  );
}

function TargetProgram({
  node,
  authoring,
  subject,
  observed,
  modelCapabilities,
  isLive,
  onPromptApply,
}: {
  node: Parameters<typeof NodeSurface>[0]["node"];
  authoring: NodeAuthoring | undefined;
  subject: ObserveSubject;
  observed: SearchpointRead;
  modelCapabilities: Parameters<typeof NodeSurface>[0]["modelCapabilities"];
  isLive: boolean;
  onPromptApply: ((patch: DraftPatch) => void) | undefined;
}) {
  const { setObserve } = useSelection();
  if (authoring) {
    const scoped = node != null;
    return (
      <NodeSurface
        node={scoped ? node : null}
        point={{ origin_prompts: authoring.prompts, pipeline_overlay: {} }}
        overlay={scoped ? authoring.overlay : {}}
        modelCapabilities={modelCapabilities}
        mode={scoped ? "search-space" : "values"}
        onApply={scoped ? onPromptApply : undefined}
      />
    );
  }

  const cfg = observed.cfg;

  return (
    <>
      {subject.options.length > 1 && (
        <div className="observe-toggle">
          <span className="observe-toggle-label">Searchpoint</span>
          <SegmentedControl<ObserveState>
            options={subject.options}
            value={subject.state}
            onChange={setObserve}
            ariaLabel="Which searchpoint to show"
          />
        </div>
      )}
      {cfg ? (
        <>
          <NodeSurface
            node={node}
            point={{ origin_prompts: cfg.prompts, pipeline_overlay: {} }}
            overlay={cfg.config}
            modelCapabilities={modelCapabilities}
            label={cfg.label}
            mode="values"
          />
          {Object.keys(cfg.prompts).length === 0 && (
            <p className="inspector-note">
              The optimizer has not changed this node&apos;s prompt — it still runs the
              one its dataset shipped.
            </p>
          )}
        </>
      ) : observed.read.status === "failed" ? (
        <FailureLine
          failure={observed.read.failure}
          lead="Could not read this searchpoint."
          className="inspector-note"
        />
      ) : (
        <p className="inspector-note">
          {observed.read.status === "loading"
            ? "Loading the searchpoint…"
            : isLive
              ? "Scoring in progress — the resolved spec appears when the round closes."
              : "No measured searchpoint yet."}
        </p>
      )}
    </>
  );
}

// Dispatch on the served `kind`, never block shape: an unfired LLM node has a measurement node's.
// Only an LLM call leaves an audit block, so any other kind has no run to show.
function RunSection({
  kind,
  block,
  round,
  loading,
  kindLoading,
  inFlight,
}: {
  kind: PipelineViewNode["kind"] | null;
  block: NodeBlock | null;
  round: number;
  loading: boolean;
  kindLoading: boolean;
  inFlight: boolean;
}) {
  if (kind != null && kind !== "measurement" && kind !== "llm") return null;
  return (
    <>
      <hr className="setup-preview-divider" />
      {kind == null ? (
        kindLoading ? (
          <Loading className="opt-detail-empty">Reading the optimizer manifest…</Loading>
        ) : (
          <Empty className="opt-detail-empty">This node is not in the served pipeline.</Empty>
        )
      ) : kind === "measurement" ? (
        <MeasurementRun round={round} />
      ) : (
        <CallRun block={block} loading={loading} inFlight={inFlight} />
      )}

      {block && (
        <footer className="opt-detail-footer">
          <details className="opt-detail-disclosure">
            <summary>raw block</summary>
            <pre className="opt-detail-pre">{fmtValue(block, { pretty: true })}</pre>
          </details>
        </footer>
      )}
    </>
  );
}

function CallRun({
  block,
  loading,
  inFlight,
}: {
  block: NodeBlock | null;
  loading: boolean;
  inFlight: boolean;
}) {
  const templateFields = Object.entries(block?.input.template_fields ?? {});
  const response = block?.output.response;
  const reasoning = block?.output.reasoning;
  const usage = block?.usage;
  const variants = variantsOf(response);
  const prefix = block?.prefix;

  // The ask carries the routing suffix; OpenRouter echoes `block.model` bare, dropping `:nitro`.
  const asked = block?.config["model"];
  const model = (typeof asked === "string" ? asked : block?.model) || "";

  const chips = [
    { label: "model", value: model },
    { label: "dur", value: block ? fmtSecs(block.duration_s) : "" },
    {
      label: "tokens",
      value: usage ? `${usage.input}in / ${usage.output}out / ${usage.input + usage.output}t` : "",
    },
    // Never labelled "cached": app-wide that means OUR archive answered, the opposite fact.
    {
      label: "prefix cached",
      value:
        prefix?.state === "unreported"
          ? "not reported"
          : prefix?.share != null
            ? `${fmtUnit("rate", prefix.share)} of prompt`
            : "",
    },
    { label: "template", value: block?.input.template_name ?? "" },
    { label: "ts", value: block?.timestamp ?? "" },
  ].filter((c) => c.value !== "" && c.value !== PLACEHOLDER);

  return (
    <>
      {chips.length > 0 && (
        <div className="opt-detail-meta">
          {chips.map((c) => (
            <span key={c.label} className="opt-detail-chip">
              <span className="opt-detail-chip-label">{c.label}</span>
              <span className="opt-detail-chip-value">{c.value}</span>
            </span>
          ))}
        </div>
      )}

      {!block ? (
        loading ? (
          <Loading className="opt-detail-empty">Loading this round&apos;s audit trail…</Loading>
        ) : (
          <Empty className="opt-detail-empty">This node has not fired in any cached round yet.</Empty>
        )
      ) : (
        <>
          {variants && variants.length > 0 && <L1Variants variants={variants} />}

          <div className="opt-detail-cols">
            <section className="opt-detail-col opt-detail-col-fields" aria-label="Template fields">
              <div className="opt-detail-col-head">
                <span>Rendered input</span>
                {templateFields.length > 0 && (
                  <span className="opt-detail-col-count">{templateFields.length}</span>
                )}
              </div>
              <div className="opt-detail-col-body">
                {templateFields.length > 0 ? (
                  <dl className="opt-detail-fields">
                    {templateFields.map(([k, v]) => (
                      <div key={k} className="opt-detail-field">
                        <dt>{k}</dt>
                        <dd>
                          <pre>{fmtValue(v, { pretty: true })}</pre>
                        </dd>
                      </div>
                    ))}
                  </dl>
                ) : (
                  <Empty className="opt-detail-col-empty">No template fields on this block.</Empty>
                )}
              </div>
            </section>

            {!variants && (
              <section className="opt-detail-col opt-detail-col-response" aria-label="Response">
                <div className="opt-detail-col-head">
                  <span>Response</span>
                </div>
                <div className="opt-detail-col-body">
                  {response != null ? (
                    <pre className="opt-detail-pre">{fmtValue(response, { pretty: true })}</pre>
                  ) : inFlight ? (
                    <Loading className="opt-detail-col-empty">
                      In flight — response not yet written.
                    </Loading>
                  ) : (
                    <Empty className="opt-detail-col-empty">No response on this block.</Empty>
                  )}
                </div>
              </section>
            )}

            {reasoning && (
              <section
                className="opt-detail-col opt-detail-col-reasoning"
                aria-label="Model thinking"
              >
                <div className="opt-detail-col-head">
                  <span>Thinking</span>
                  <Term
                    className="opt-detail-col-note"
                    content="The model's own reasoning, recorded for analysis. It never feeds the optimizer's decisions — no score, gate or selection reads it."
                  >
                    analysis only
                  </Term>
                </div>
                <div className="opt-detail-col-body">
                  <pre className="opt-detail-pre opt-detail-reasoning">{reasoning}</pre>
                </div>
              </section>
            )}
          </div>
        </>
      )}
    </>
  );
}
