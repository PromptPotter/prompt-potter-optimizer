"use client";
import { useMemo } from "react";
import type { DraftPatch, StartPrompt } from "@/lib/api";
import { useSelection, type SelectedNode } from "@/lib/SelectionContext";
import { cx } from "@/lib/cx";
import type { NodeBlock } from "@/lib/api/types";
import type { NodeSchemaReading } from "@/lib/types";
import { useConnector } from "@/lib/hooks/useConnector";
import { roundOf, useCycleStream } from "@/lib/poll";
import { useObserveSubject } from "@/lib/hooks/useObserveSubject";
import { useSearchpointRead, type SearchpointRead } from "@/lib/hooks/useSearchpointRead";
import { useOptimizerPipeline } from "@/lib/hooks/useOptimizerPipeline";
import { useRoundNodes } from "@/lib/hooks/useRoundNodes";
import {
  interiorNodes,
  pipelineReadStatus,
  type ObserveState,
  type ObserveSubject,
} from "@/lib/derivations";
import { fmtPct0, fmtSecs, fmtValue } from "@/lib/format";
import { nodeKind } from "@/lib/types";
import { CopyButton, SegmentedControl } from "@/components/ui";
import { NodeSurface } from "./NodeSurface";
import { MeasurementRun } from "./MeasurementRun";
import { L1Variants, variantsOf } from "./L1Variants";

export interface NodeAuthoring {
  overlay: Record<string, unknown>;
  promptFields: Record<string, unknown>;
}

interface Props {
  node: SelectedNode;
  authoring?: NodeAuthoring;
  onClose: () => void;
  onPromptApply?: (patch: DraftPatch) => void;
}

export function NodeDetail({ node: selected, authoring, onClose, onPromptApply }: Props) {
  const { id, scope } = selected;
  const isOptimizer = scope === "optimizer";

  const cv = useConnector();
  const { dash, isLive } = useCycleStream();
  const liveRound = roundOf(dash);
  const { doc: optimizer, loading: pipelineLoading } = useOptimizerPipeline(
    isOptimizer ? cv.optimizer : null,
  );
  const subject = useObserveSubject();
  const observed = useSearchpointRead(!isOptimizer && !authoring ? subject.point : null, id);

  const view = isOptimizer ? optimizer?.view : cv.view;
  const node = interiorNodes(view).find((n) => n.id === id) ?? null;
  const servedKind = node?.kind ?? null;
  const kindInfo = servedKind ? nodeKind(servedKind) : null;
  const optimizerStatus = pipelineReadStatus({
    bound: cv.schema.status !== "unbound",
    loading: pipelineLoading || cv.schema.status === "loading",
    failed: !optimizer,
  });
  const schema = useMemo<NodeSchemaReading>(
    () =>
      isOptimizer
        ? {
            status: optimizerStatus,
            config: optimizer?.node_config_schema ?? null,
            output: optimizer?.node_output_schema ?? null,
            isSingleNode: false,
          }
        : cv.schema,
    [isOptimizer, optimizerStatus, optimizer, cv.schema],
  );
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

  const origin = optimizer?.start_prompts[id] ?? null;

  const identity = { id, scope, label: node?.label ?? id, kind: servedKind };
  const program = isOptimizer
    ? { ...identity, prompt_fields: origin?.fields ?? {} }
    : authoring
      ? {
          ...identity,
          resolved_pipeline_params: authoring.overlay,
          prompt_fields: authoring.promptFields,
        }
      : observed.cfg
        ? {
            ...identity,
            searchpoint: observed.cfg.label,
            resolved_pipeline_params: observed.cfg.config,
            prompt_fields: observed.cfg.promptFields,
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
            {kindInfo && <span className={cx("bnode-kind", kindInfo.cls)}>{kindInfo.label}</span>}
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

        {kindInfo && <p className="bnode-role">{kindInfo.role}</p>}

        {isOptimizer ? (
          <OptimizerProgram
            node={node}
            origin={origin}
            schema={schema}
            modelCapabilities={modelCapabilities}
          />
        ) : (
          <TargetProgram
            node={node}
            authoring={authoring}
            subject={subject}
            observed={observed}
            schema={schema}
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

function OptimizerProgram({
  node,
  origin,
  schema,
  modelCapabilities,
}: {
  node: Parameters<typeof NodeSurface>[0]["node"];
  origin: StartPrompt | null;
  schema: NodeSchemaReading;
  modelCapabilities: Parameters<typeof NodeSurface>[0]["modelCapabilities"];
}) {
  return (
    <>
      <NodeSurface
        node={node}
        point={{ origin_prompt_fields: origin?.fields ?? {}, pipeline_overlay: {} }}
        overlay={{}}
        schema={schema}
        modelCapabilities={modelCapabilities}
        mode="values"
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
  schema,
  modelCapabilities,
  isLive,
  onPromptApply,
}: {
  node: Parameters<typeof NodeSurface>[0]["node"];
  authoring?: NodeAuthoring;
  subject: ObserveSubject;
  observed: SearchpointRead;
  schema: NodeSchemaReading;
  modelCapabilities: Parameters<typeof NodeSurface>[0]["modelCapabilities"];
  isLive: boolean;
  onPromptApply?: (patch: DraftPatch) => void;
}) {
  const { setObserve } = useSelection();
  if (authoring) {
    const scoped = node != null;
    return (
      <NodeSurface
        node={scoped ? node : null}
        point={{ origin_prompt_fields: authoring.promptFields, pipeline_overlay: {} }}
        overlay={scoped ? authoring.overlay : {}}
        schema={schema}
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
            point={{ origin_prompt_fields: cfg.promptFields, pipeline_overlay: {} }}
            overlay={cfg.config}
            schema={schema}
            modelCapabilities={modelCapabilities}
            label={cfg.label}
            mode="values"
          />
          {Object.keys(cfg.promptFields).length === 0 && (
            <p className="inspector-note">
              The optimizer has not changed this node&apos;s prompt — it still runs the
              one its dataset shipped.
            </p>
          )}
        </>
      ) : (
        <p className="inspector-note">
          {observed.loading
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
    { label: "call", value: block?.synthesized ? "none — banked answer replayed" : "" },
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
            ? `${fmtPct0(prefix.share)} of prompt`
            : "",
    },
    { label: "template", value: block?.input.template_name ?? "" },
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
