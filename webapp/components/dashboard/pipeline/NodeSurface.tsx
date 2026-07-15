"use client";
import type { ReactNode } from "react";
import type { DraftPatch, NodeConfigParam, NodeOutputSchema } from "@/lib/api";
import type { CandidateSearchPoint, ConfigMode } from "@/lib/derivations";
import type { PipelineViewNode } from "@/components/workflow";
import { PromptFieldsEditor } from "@/components/dashboard/control/PromptFieldsEditor";
import { NodeConfigEditor } from "@/components/dashboard/control/NodeConfigEditor";
import { NodeOutputSchemaView } from "@/components/dashboard/control/NodeOutputSchemaView";

// The one node surface: config → prompt → output, rendered as an inseparable
// unit so config can never be gated away from its prompt. Used everywhere a
// node's program is shown or edited:
//   - a concrete view node (`node` set) → config scoped to that node;
//   - the whole pipeline (`node === null`) → config across every node + the
//     prompt (the single-LLM chip / steer-fork seed).
// `mode` picks the config lever (search-space lock/allow vs concrete values).
// `readOnly` (or the absence of the matching callback) makes it inspect-only.
// `flat` drops the card chrome so it can embed inside another panel (the steer
// dialog). The prompt renders for `llm` nodes and for the whole-pipeline view.
const KIND_ROLE: Record<string, string> = {
  llm: "LLM call — runs the prompt below against each query.",
  retriever: "Retriever — ranks candidates from the index by similarity. No prompt.",
  tool: "Tool — fetches external context (e.g. web search) for downstream nodes. No prompt.",
  cache: "Cache — short-circuits the pipeline on a known hit. No prompt.",
};

export function NodeSurface({
  node,
  point,
  configSeed,
  schema,
  outputSchema,
  label,
  title,
  mode,
  babysitEditable,
  toggle,
  readOnly,
  flat,
  onClose,
  onApply,
  onConfigChange,
}: {
  // A concrete pipeline node, or null for the whole-pipeline view.
  node: PipelineViewNode | null;
  // The searchpoint whose prompt fields are shown/edited.
  point: CandidateSearchPoint;
  // The overlay seeding the config editor — the draft optimizer overlay in
  // search-space mode (`{}` on read-only inspection), the candidate's evolved
  // values in values mode. Distinct from `point.pipeline_overlay` (the prompt's
  // searchpoint) so search-space inspection isn't seeded by a values overlay.
  configSeed: Record<string, unknown>;
  schema: Record<string, NodeConfigParam[]> | null;
  outputSchema: Record<string, NodeOutputSchema | null> | null;
  label?: string;
  // Header title for the whole-pipeline view (ignored when `node` is set).
  title?: string;
  mode: ConfigMode;
  // values mode: gates editing of optimizer-locked axes (model/provider) behind the
  // operator's `campaign.babysit` capability. Default (undefined) leaves them editable.
  babysitEditable?: boolean;
  // Optional control rendered inside the card, between the role line and the
  // config body (the OBSERVE origin/live/historical selector).
  toggle?: ReactNode;
  readOnly?: boolean;
  flat?: boolean;
  onClose?: () => void;
  // Prompt edits + search-space config edits ride this DraftPatch. Values-mode
  // config edits ride `onConfigChange` (the flat fork overlay). Absent → that
  // surface is read-only.
  onApply?: (patch: DraftPatch) => void;
  onConfigChange?: (overlay: Record<string, Record<string, unknown>>) => void;
}) {
  const kind = node?.kind;
  const role = node ? (kind ? KIND_ROLE[kind] ?? "Pipeline node." : "Pipeline node.") : null;
  // Prompt shows for `llm` nodes and for the whole-pipeline view (the single-LLM
  // chip / steer seed both carry one). Scope the output contract to THIS node, or
  // the full set for the whole-pipeline view.
  const showPrompt = kind === "llm" || node === null;
  // A node whose id is absent from the served map declares no output contract —
  // distinct from a declared-but-null one, which renders as such.
  const ownOutput = node && outputSchema ? outputSchema[node.id] : undefined;
  const nodeOutput = node
    ? ownOutput === undefined
      ? null
      : { [node.id]: ownOutput }
    : outputSchema;

  const promptReadOnly = !!readOnly || !onApply;
  const configReadOnly =
    !!readOnly || (mode === "search-space" ? !onApply : !onConfigChange);

  const body = (
    <>
      {toggle}
      <NodeConfigEditor
        mode={mode}
        schema={schema}
        node={node?.id}
        seedOverlay={configSeed}
        babysitEditable={babysitEditable}
        readOnly={configReadOnly}
        onApply={onApply}
        onChange={onConfigChange}
      />

      {showPrompt ? (
        <>
          <hr className="setup-preview-divider" />
          <PromptFieldsEditor
            value={point.origin_prompt_fields}
            flat
            readOnly={promptReadOnly}
            onApply={promptReadOnly ? undefined : onApply}
          />
        </>
      ) : null}

      <NodeOutputSchemaView schema={nodeOutput} />
    </>
  );

  if (flat) return body;

  return (
    <div className="bnode">
      <section className="setup-preview">
        <header className="setup-preview-head">
          <span className="setup-preview-title">
            {node ? (
              <>
                {/* Kind chip rendered only when the node declares one. */}
                {kind ? <span className={`bnode-kind kind-${kind}`}>{kind}</span> : null}
                {node.label}
              </>
            ) : (
              (title ?? "Pipeline, optimizer & prompt")
            )}
          </span>
          <span className="setup-preview-side">
            {label ? <span className="setup-preview-sub">{label}</span> : null}
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

        {role ? <p className="bnode-role">{role}</p> : null}

        {body}
      </section>
    </div>
  );
}
