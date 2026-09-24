"use client";
import type { DraftPatch } from "@/lib/api";
import type { SelectedNode } from "@/lib/SelectionContext";
import { cx } from "@/lib/cx";
import type { NodeBlock } from "@/lib/types";
import { useConnector } from "@/lib/hooks/useConnector";
import { useDashboard } from "@/lib/hooks/useDashboard";
import { useObserveSearchPoint } from "@/lib/hooks/useObserveSearchPoint";
import { useOptimizerPipeline } from "@/lib/hooks/useOptimizerPipeline";
import { useRoundNodes } from "@/lib/hooks/useRoundNodes";
import {
  cacheShare,
  interiorNodes,
  nodeOriginPrompt,
  observeOptions,
  pipelineReadStatus,
  prefixReading,
  type ObserveState,
} from "@/lib/derivations";
import { fmtPct0, fmtSecs, fmtValue } from "@/lib/format";
import { nodeKind } from "@/components/workflow";
import { CopyButton, SegmentedControl } from "@/components/ui";
import { NodeSurface } from "./NodeSurface";
import { MeasurementRun } from "./MeasurementRun";
import { L1Variants, variantsOf } from "./L1Variants";

// The one node detail for every tab: PROGRAM (what it is) and, optimizer-scoped only, RUN (what it
// did). The audit twin records the optimizer's calls, and pp-self's target reuses its node ids.

// Its presence is the MODE; its fields are documents, never a store — config rows still come from
// `useConnector()` (`frontend-surface-contract.md::I9`).
export interface NodeAuthoring {
  overlay: Record<string, unknown>;
  promptFields: Record<string, unknown>;
}

interface Props {
  node: SelectedNode;
  // Target scope only.
  authoring?: NodeAuthoring;
  onClose: () => void;
  onPromptApply?: (patch: DraftPatch) => void;
}

export function NodeDetail({ node: selected, authoring, onClose, onPromptApply }: Props) {
  const { id, scope } = selected;
  const isOptimizer = scope === "optimizer";

  const cv = useConnector();
  const { dash, isLive, dashRound: liveRound } = useDashboard();
  const { doc: optimizer, loading: pipelineLoading } = useOptimizerPipeline(isOptimizer);
  const observe = useObserveSearchPoint(id, !isOptimizer && !authoring);

  const view = isOptimizer ? optimizer?.view : cv.view;
  const node = interiorNodes(view).find((n) => n.id === id) ?? null;
  // Not defaulted: the run half dispatches on it. The header caption takes `nodeKind`'s fallback,
  // which mirrors `pipeline_parsing.py::_derive_node_kind`.
  const servedKind = node?.kind ?? null;
  const kindInfo = nodeKind(servedKind ?? undefined);
  const schema = isOptimizer ? (optimizer?.node_config_schema ?? null) : cv.nodeConfigSchema;
  // Two fetches back this panel; `cv.pipelineStatus` answers for the campaign's alone.
  const schemaStatus = isOptimizer
    ? pipelineReadStatus({ bound: true, loading: pipelineLoading, failed: !optimizer })
    : cv.pipelineStatus;
  const outputSchema = isOptimizer
    ? (optimizer?.node_output_schema ?? null)
    : cv.nodeOutputSchema;
  // Off the SAME read as the rows it qualifies, never fetched separately.
  const modelCapabilities = isOptimizer
    ? (optimizer?.model_capabilities ?? {})
    : cv.modelCapabilities;

  const {
    nodes: roundNodes,
    round: viewedRound,
    showsCurrent: viewingLive,
    loading: nodesLoading,
  } = useRoundNodes();
  const block: NodeBlock | null = isOptimizer ? (roundNodes[id] ?? null) : null;

  // Resolved here, not in `OptimizerProgram`: the header's copy and the body must show one prompt.
  const origin = nodeOriginPrompt(optimizer, id);

  // RUN is dropped rather than offered empty until the node fires.
  const identity = { id, scope, label: node?.label ?? id, kind: servedKind };
  const program = isOptimizer
    ? { ...identity, prompt_fields: origin?.fields ?? {} }
    : authoring
      ? {
          ...identity,
          resolved_pipeline_params: authoring.overlay,
          prompt_fields: authoring.promptFields,
        }
      : observe.cfg
        ? {
            ...identity,
            searchpoint: observe.cfg.label,
            resolved_pipeline_params: observe.cfg.config,
            prompt_fields: observe.cfg.promptFields,
          }
        : identity;
  const copyChoices = [
    { key: "program", label: "What this node runs", data: program },
    ...(block ? [{ key: "run", label: `What it did in round ${viewedRound}`, data: block }] : []),
  ];

  const livePhaseNode = dash?.current_round.active_node ?? null;
  const isLiveNow = isLive && viewingLive && livePhaseNode === id && isOptimizer;

  return (
    <div className={cx("bnode", isOptimizer && "bnode-wide")}>
      <section className="setup-preview">
        <header className="setup-preview-head">
          <span className="setup-preview-title">
            <span className={cx("bnode-kind", kindInfo.cls)}>{kindInfo.label}</span>
            {node?.label ?? id}
            <code className="opt-detail-id">{id}</code>
          </span>
          {/* A div: `CopyButton`'s choices menu opens a `Popover`, which is flow content. */}
          <div className="setup-preview-side">
            <span className={cx("opt-detail-status", isLiveNow && "live")}>
              ● {isLiveNow ? `live · round ${liveRound ?? "—"}` : scopeLabel(isOptimizer)}
            </span>
            {isOptimizer && viewedRound != null && (
              <span
                className="opt-detail-round-tag"
                title="Round is set by the round axis beside the pipeline picture"
              >
                round {viewedRound}
                {viewingLive ? " · live" : ""}
              </span>
            )}
            <CopyButton choices={copyChoices} title="Copy this node as JSON" />
            <button
              type="button"
              className="bnode-close"
              onClick={onClose}
              aria-label="Close detail"
              title="Close"
            >
              ×
            </button>
          </div>
        </header>

        <p className="bnode-role">{kindInfo.role}</p>

        {isOptimizer ? (
          <OptimizerProgram
            node={node}
            origin={origin}
            schema={schema}
            schemaStatus={schemaStatus}
            outputSchema={outputSchema}
            modelCapabilities={modelCapabilities}
          />
        ) : (
          <TargetProgram
            node={node}
            authoring={authoring}
            isSingleNode={cv.isSingleNode}
            observe={observe}
            schema={schema}
            schemaStatus={schemaStatus}
            outputSchema={outputSchema}
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
            loading={nodesLoading}
            kindLoading={pipelineLoading}
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

// Read-only by construction: the optimizer manifest is one operator-owned file with no draft.
function OptimizerProgram({
  node,
  origin,
  schema,
  schemaStatus,
  outputSchema,
  modelCapabilities,
}: {
  node: Parameters<typeof NodeSurface>[0]["node"];
  origin: ReturnType<typeof nodeOriginPrompt>;
  schema: Parameters<typeof NodeSurface>[0]["schema"];
  schemaStatus: Parameters<typeof NodeSurface>[0]["schemaStatus"];
  outputSchema: Parameters<typeof NodeSurface>[0]["outputSchema"];
  modelCapabilities: Parameters<typeof NodeSurface>[0]["modelCapabilities"];
}) {
  return (
    <>
      <NodeSurface
        node={node}
        point={{ origin_prompt_fields: origin?.fields ?? {}, pipeline_overlay: {} }}
        overlay={{}}
        schema={schema}
        schemaStatus={schemaStatus}
        outputSchema={outputSchema}
        modelCapabilities={modelCapabilities}
        mode="values"
      />
      {origin && origin.count > 1 && (
        <p className="inspector-note">
          Showing prompt {origin.version} of {origin.count} this node declares.
        </p>
      )}
    </>
  );
}

// Dispatches on LIFECYCLE: authoring a draft, previewing it whole, or observing a measured searchpoint.
function TargetProgram({
  node,
  authoring,
  isSingleNode,
  observe,
  schema,
  schemaStatus,
  outputSchema,
  modelCapabilities,
  isLive,
  onPromptApply,
}: {
  node: Parameters<typeof NodeSurface>[0]["node"];
  authoring?: NodeAuthoring;
  isSingleNode: boolean;
  observe: ReturnType<typeof useObserveSearchPoint>;
  schema: Parameters<typeof NodeSurface>[0]["schema"];
  schemaStatus: Parameters<typeof NodeSurface>[0]["schemaStatus"];
  outputSchema: Parameters<typeof NodeSurface>[0]["outputSchema"];
  modelCapabilities: Parameters<typeof NodeSurface>[0]["modelCapabilities"];
  isLive: boolean;
  onPromptApply?: (patch: DraftPatch) => void;
}) {
  if (authoring) {
    const scoped = node != null;
    return (
      <NodeSurface
        node={scoped ? node : null}
        point={{ origin_prompt_fields: authoring.promptFields, pipeline_overlay: {} }}
        overlay={scoped ? authoring.overlay : {}}
        isSingleNode={isSingleNode}
        schema={schema}
        schemaStatus={schemaStatus}
        outputSchema={outputSchema}
        modelCapabilities={modelCapabilities}
        mode={scoped ? "search-space" : "values"}
        onApply={scoped ? onPromptApply : undefined}
      />
    );
  }

  const options = observeOptions(observe.avail);

  return (
    <>
      {options.length > 1 && (
        <div className="observe-toggle">
          <span className="observe-toggle-label">Searchpoint</span>
          <SegmentedControl<ObserveState>
            options={options}
            value={observe.state}
            onChange={observe.setPref}
            ariaLabel="Which searchpoint to show"
          />
        </div>
      )}
      {observe.cfg ? (
        <>
          <NodeSurface
            node={node}
            point={{ origin_prompt_fields: observe.cfg.promptFields, pipeline_overlay: {} }}
            overlay={observe.cfg.config}
            schema={schema}
            schemaStatus={schemaStatus}
            outputSchema={outputSchema}
            modelCapabilities={modelCapabilities}
            label={observe.cfg.label}
            mode="values"
          />
          {/* The prompt is the optimizer's evolved DELTA: empty means untouched, not "runs on nothing". */}
          {Object.keys(observe.cfg.promptFields).length === 0 && (
            <p className="inspector-note">
              The optimizer has not changed this node&apos;s prompt — it still runs the
              one its dataset shipped.
            </p>
          )}
        </>
      ) : (
        <p className="inspector-note">
          {observe.loading
            ? "Loading the searchpoint…"
            : isLive
              ? "Scoring in progress — the resolved spec appears when the round closes."
              : "No measured searchpoint yet."}
        </p>
      )}
    </>
  );
}

// Dispatch on the served `kind`, never block shape: an unfired LLM node lacks the same keys a
// measurement node does.
function RunSection({
  kind,
  block,
  round,
  loading,
  kindLoading,
  inFlight,
}: {
  kind: string | null;
  block: NodeBlock | null;
  round: number;
  loading: boolean;
  kindLoading: boolean;
  inFlight: boolean;
}) {
  return (
    <>
      <hr className="setup-preview-divider" />
      {kind == null ? (
        <div className="opt-detail-empty">
          {kindLoading
            ? "Reading the optimizer manifest…"
            : "This node is not in the served pipeline."}
        </div>
      ) : kind === "measurement" ? (
        <MeasurementRun block={block} round={round} />
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
  const templateFields = block?.input?.template_fields as Record<string, unknown> | undefined;
  const response = block?.output?.response;
  const reasoning = typeof block?.output?.reasoning === "string" ? block.output.reasoning : null;
  const usage = block?.usage;
  const variants = variantsOf(response);
  const prefix = prefixReading(
    cacheShare(usage?.cache_read, usage?.input, !!block?.cached),
    !!block?.cached,
  );

  // The ask carries the routing suffix; OpenRouter echoes `block.model` bare, dropping `:nitro`.
  const asked = block?.config?.["model"];
  const model = (typeof asked === "string" ? asked : block?.model) || "";

  const chips = [
    { label: "model", value: model },
    { label: "dur", value: block ? fmtSecs(block.duration_s) : "" },
    {
      label: "tokens",
      value: usage
        ? `${usage.input ?? "—"}in / ${usage.output ?? "—"}out / ${(usage.input ?? 0) + (usage.output ?? 0)}t`
        : "",
    },
    // Never labelled "cached": app-wide that means OUR archive answered, the opposite fact.
    {
      label: "prefix cached",
      value:
        prefix.state === "unreported"
          ? "not reported"
          : prefix.share != null
            ? `${fmtPct0(prefix.share)} of prompt`
            : "",
    },
    { label: "template", value: (block?.input?.template_name as string | undefined) ?? "" },
    { label: "ts", value: block?.timestamp ?? "" },
  ].filter((c) => c.value !== "" && c.value !== "—");

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
        <div className="opt-detail-empty">
          {loading
            ? "Loading this round's audit trail…"
            : "This node has not fired in any cached round yet."}
        </div>
      ) : (
        <>
          {variants && variants.length > 0 && <L1Variants variants={variants} />}

          <div className="opt-detail-cols">
            <section className="opt-detail-col opt-detail-col-fields" aria-label="Template fields">
              <div className="opt-detail-col-head">
                <span>Rendered input</span>
                {templateFields && (
                  <span className="opt-detail-col-count">
                    {Object.keys(templateFields).length}
                  </span>
                )}
              </div>
              <div className="opt-detail-col-body">
                {templateFields && Object.keys(templateFields).length > 0 ? (
                  <dl className="opt-detail-fields">
                    {Object.entries(templateFields).map(([k, v]) => (
                      <div key={k} className="opt-detail-field">
                        <dt>{k}</dt>
                        <dd>
                          <pre>{fmtValue(v, { pretty: true })}</pre>
                        </dd>
                      </div>
                    ))}
                  </dl>
                ) : (
                  <div className="opt-detail-col-empty">No template fields on this block.</div>
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
                    <div className="opt-detail-col-empty">
                      In flight — response not yet written.
                    </div>
                  ) : (
                    <div className="opt-detail-col-empty">No response on this block.</div>
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
                  <span
                    className="opt-detail-col-note"
                    title="The model's own reasoning, recorded for analysis. It never feeds the optimizer's decisions — no score, gate or selection reads it."
                  >
                    analysis only
                  </span>
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
