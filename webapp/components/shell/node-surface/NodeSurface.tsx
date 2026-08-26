"use client";
import type { DraftPatch, NodeConfigParam, NodeOutputSchema } from "@/lib/api";
import type { CandidateSearchPoint, ConfigMode } from "@/lib/derivations";
import type { PipelineViewNode } from "@/components/workflow";
import { PromptFieldsEditor } from "./PromptFieldsEditor";
import { NodeConfigEditor } from "./NodeConfigEditor";
import { NodeOutputSchemaView } from "./NodeOutputSchemaView";

// The one node surface: config → prompt → output, rendered as an inseparable unit so
// config can never be gated away from its prompt. It renders exactly ONE runnable
// specification — WHICH searchpoint that is, is the host's decision, made outside this
// box. Used everywhere a node's program is shown or edited:
//   - a concrete view node (`node` set) → config scoped to that node;
//   - the whole pipeline (`node === null`) → config across every node + the prompt
//     (the single-LLM chip / steer-fork seed).
// `mode` picks the config lever (search-space lock/allow vs concrete values). It draws NO
// chrome of its own — every host already frames it, so a header here is a second one.
export function NodeSurface({
  node,
  point,
  configSeed,
  schema,
  outputSchema,
  label,
  mode,
  babysitEditable,
  compact,
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
  // WHICH searchpoint is on screen ("best", "most recent", …). Rendered here because
  // nothing else on screen names it.
  label?: string;
  mode: ConfigMode;
  // values mode: gates editing of optimizer-locked axes (model/provider) behind the
  // operator's `campaign.babysit` capability. Default (undefined) leaves them editable.
  babysitEditable?: boolean;
  // Half-width host (the chat run card): params at their default fold away, prompt
  // boxes shorten, the output contract collapses. It changes what is IN VIEW, never
  // what exists — every part stays one disclosure away.
  compact?: boolean;
  // Prompt edits + search-space config edits ride this DraftPatch. Values-mode config
  // edits ride `onConfigChange` (the flat fork overlay). **Absence IS read-only** — there
  // is no second flag for it, so no host can claim editable while passing no callback.
  onApply?: (patch: DraftPatch) => void;
  onConfigChange?: (overlay: Record<string, Record<string, unknown>>) => void;
}) {
  const kind = node?.kind;
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

  const readOnly = !onApply;
  const configReadOnly = mode === "search-space" ? !onApply : !onConfigChange;

  return (
    <>
      {label ? <p className="setup-preview-sub">{label}</p> : null}

      <NodeConfigEditor
        mode={mode}
        schema={schema}
        node={node?.id}
        seedOverlay={configSeed}
        babysitEditable={babysitEditable}
        readOnly={configReadOnly}
        compact={compact}
        onApply={onApply}
        onChange={onConfigChange}
      />

      {showPrompt ? (
        <>
          <hr className="setup-preview-divider" />
          <PromptFieldsEditor
            value={point.origin_prompt_fields}
            readOnly={readOnly}
            compact={compact}
            onApply={readOnly ? undefined : onApply}
          />
        </>
      ) : null}

      {compact ? (
        <details className="node-output-fold">
          <summary>Output contract</summary>
          <NodeOutputSchemaView schema={nodeOutput} />
        </details>
      ) : (
        <NodeOutputSchemaView schema={nodeOutput} />
      )}
    </>
  );
}
