"use client";
import type { DraftPatch, NodeConfigParam, NodeOutputSchema } from "@/lib/api";
import type { CandidateSearchPoint } from "@/lib/derivations";
import type { PipelineViewNode } from "@/components/workflow";
import { PromptFieldsEditor } from "@/components/dashboard/control/PromptFieldsEditor";
import { NodeLockEditor } from "@/components/dashboard/control/NodeLockEditor";
import { NodeOutputSchemaView } from "@/components/dashboard/control/NodeOutputSchemaView";

// Per-node detail surface — the generalization of the old prompt-only preview.
// EVERY node renders its own config + output contract; only `llm`-kind nodes
// additionally carry a prompt (the optimized searchpoint fields). Retriever /
// tool / cache nodes surface what they actually are — config + the
// candidates/sources/hit they emit — instead of an empty prompt block, which is
// why only `llm_only` used to look "right". Read-only; never forks (steering
// lives in ScoringInspector → SteerForkPanel).
const KIND_ROLE: Record<string, string> = {
  llm: "LLM call — runs the prompt below against each query.",
  retriever: "Retriever — ranks candidates from the index by similarity. No prompt.",
  tool: "Tool — fetches external context (e.g. web search) for downstream nodes. No prompt.",
  cache: "Cache — short-circuits the pipeline on a known hit. No prompt.",
};

export function NodeSurface({
  node,
  point,
  schema,
  outputSchema,
  label,
  overlayBase,
  lockModel,
  onClose,
  onApply,
}: {
  node: PipelineViewNode;
  point: CandidateSearchPoint;
  schema: Record<string, NodeConfigParam[]> | null;
  outputSchema: Record<string, NodeOutputSchema | null> | null;
  label: string;
  // The draft's accumulated `pipeline_overlay` (the operator's in-progress
  // lock/allow + origin-value edits, keyed by node) and the campaign-wide model
  // lock — the lock editor's editable state, layered on the served schema.
  overlayBase: Record<string, unknown>;
  lockModel: boolean;
  onClose?: () => void;
  // When set (campaign setup on a draft), the node's lock/allow controls AND the
  // LLM prompt are EDITABLE and persist via this draft patch. Absent → read-only
  // (the minted-campaign / live inspection case).
  onApply?: (patch: DraftPatch) => void;
}) {
  // No `?? "tool"` default — a kindless node must not masquerade as a Tool
  // ("Tool — …No prompt" would be a lie). The chip + role render only what the
  // node actually declares; an unknown kind falls back to the neutral role.
  const kind = node.kind;
  const isLlm = kind === "llm";
  const role = kind ? KIND_ROLE[kind] ?? "Pipeline node." : "Pipeline node.";
  // Scope config + output contract to THIS node — the panel is per-node now, not
  // a whole-pipeline searchpoint dump.
  const nodeParams = schema && node.id in schema ? schema[node.id] : [];
  const nodeOutput =
    outputSchema && node.id in outputSchema ? { [node.id]: outputSchema[node.id] } : null;

  return (
    <div className="bnode">
      <section className="setup-preview">
        <header className="setup-preview-head">
          <span className="setup-preview-title">
            {/* Render the kind chip only when the node declares one. `kind-${kind}`
                is the per-kind styling hook the upcoming styling pass colors. */}
            {kind ? <span className={`bnode-kind kind-${kind}`}>{kind}</span> : null}
            {node.label}
          </span>
          <span className="setup-preview-side">
            <span className="setup-preview-sub">{label}</span>
            {onClose ? (
              <button
                type="button"
                className="bnode-close"
                onClick={onClose}
                aria-label="Close detail"
                title="Close"
              >
                ×
              </button>
            ) : null}
          </span>
        </header>

        <p className="bnode-role">{role}</p>

        {/* The optimizer search-space surface — what the operator lets the
            optimizer MOVE on this node (per-param lock/allow + origin value). The
            first thing shown; editable on a draft, read-only on inspection. */}
        <NodeLockEditor
          node={node.id}
          params={nodeParams}
          overlayBase={overlayBase}
          lockModel={lockModel}
          onApply={onApply}
          readOnly={!onApply}
        />

        {/* Prompt is the node's INPUT — show it above the structured output it
            produces (and only for `llm` nodes, which actually run a prompt). */}
        {isLlm ? (
          <>
            <hr className="setup-preview-divider" />
            {onApply ? (
              <PromptFieldsEditor value={point.origin_prompt_fields} onApply={onApply} flat />
            ) : (
              <PromptFieldsEditor value={point.origin_prompt_fields} readOnly flat />
            )}
          </>
        ) : null}

        <NodeOutputSchemaView schema={nodeOutput} />
      </section>
    </div>
  );
}
