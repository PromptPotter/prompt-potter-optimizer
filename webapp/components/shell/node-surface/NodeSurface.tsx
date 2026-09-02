"use client";
import type { DraftPatch, NodeConfigParam, NodeOutputSchema } from "@/lib/api";
import type { CandidateSearchPoint, ConfigMode } from "@/lib/derivations";
import { outputContract } from "@/lib/derivations";
import type { PipelineViewNode } from "@/components/workflow";
import { PromptFieldsEditor } from "./PromptFieldsEditor";
import { NodeConfigEditor } from "./NodeConfigEditor";

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
          <OutputContract schema={nodeOutput} />
        </details>
      ) : (
        <OutputContract schema={nodeOutput} />
      )}
    </>
  );
}

// The structured output this node is contracted to return — every parameter, not just the
// top-level keys. It lives in this file because the surface above renders config → prompt →
// output as one unit, and a separate component is what lets one of the three drift into its own,
// thinner reading of the same schema. `outputContract` flattens it; this only lays the rows out.
//
// Read-only by definition: the contract is a backend fact, not an operator knob.
function OutputContract({
  schema,
}: {
  schema: Record<string, NodeOutputSchema | null> | null;
}) {
  const nodes = Object.entries(schema ?? {})
    .map(([node, out]) => [node, outputContract(out)] as const)
    .filter(([, fields]) => fields.length > 0);
  if (nodes.length === 0) return null;

  return (
    <div className="node-output-schema">
      <span className="node-output-title">Structured output</span>
      {nodes.map(([node, fields]) => (
        <dl key={node} className="node-output-list" aria-label={`${node} output schema`}>
          {fields.map((f) => (
            <div key={f.key} className="node-output-row" data-depth={f.depth}>
              <dt className="node-output-field">
                <code>{f.name}</code>
                {/* Required is marked, never optional — most parameters are required, so
                    marking the majority is noise that says nothing about the minority. */}
                {f.required && (
                  <span className="node-output-req" title="Required">
                    *
                  </span>
                )}
              </dt>
              <dd className="node-output-desc">
                {f.type && <span className="node-output-type">{f.type}</span>}
                {f.limit && <span className="node-output-limit">{f.limit}</span>}
                {f.enums.length > 0 && (
                  <span className="node-output-enum">{f.enums.join(" · ")}</span>
                )}
                {f.description}
              </dd>
            </div>
          ))}
        </dl>
      ))}
    </div>
  );
}
