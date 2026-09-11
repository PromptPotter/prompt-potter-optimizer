"use client";
import type { DraftPatch, ModelCapability, NodeConfigParam, NodeOutputSchema } from "@/lib/api";
import type { NodeSearchNarrowing } from "@/lib/api/types";
import type { PipelineStatus } from "@/lib/types";
import type { CandidateSearchPoint, ConfigMode } from "@/lib/derivations";
import {
  authoredAnswerField,
  authoredOutputSchema,
  DESCRIPTION_PREFIX,
  descriptionSubtree,
  nodeLockPatch,
  nodeSchemaPatch,
  outputContract,
} from "@/lib/derivations";
import { Button } from "@/components/ui";
import type { PipelineViewNode } from "@/components/workflow";
import { PromptFieldsEditor } from "./PromptFieldsEditor";
import { LockButton, NodeConfigEditor } from "./NodeConfigEditor";
import { SchemaTreeEditor } from "./SchemaTreeEditor";

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
  overlay,
  isSingleNode,
  schema,
  schemaStatus,
  outputSchema,
  label,
  mode,
  babysitEditable,
  compact,
  modelCapabilities,
  permittedModels,
  onApply,
  onConfigChange,
  onNarrowing,
}: {
  // A concrete pipeline node, or null for the whole-pipeline view.
  node: PipelineViewNode | null;
  // The searchpoint whose prompt fields are shown/edited.
  point: CandidateSearchPoint;
  // The node-config document the editor works on — the draft's optimizer overlay in
  // search-space mode (which MERGES onto it and reads its rows from the served schema), the
  // candidate's evolved values in values mode (which seeds its rows from it). Distinct from
  // `point.pipeline_overlay`, the prompt's searchpoint.
  overlay: Record<string, unknown>;
  // Served, never counted here. See `NodeConfigEditor`.
  isSingleNode?: boolean;
  schema: Record<string, NodeConfigParam[]> | null;
  // How the read that produced `schema` went. Travels WITH it — without that, a read still in
  // flight and a node with nothing to configure are one sentence downstream.
  schemaStatus: PipelineStatus;
  outputSchema: Record<string, NodeOutputSchema | null> | null;
  // WHICH searchpoint is on screen ("best", "most recent", …). Rendered here because
  // nothing else on screen names it.
  label?: string;
  mode: ConfigMode;
  // values mode: gates editing of optimizer-locked axes (model/provider) behind the
  // operator's `campaign.babysit` capability. Default (undefined) leaves them editable.
  babysitEditable?: boolean;
  // Half-width host (the chat run card): denser config rows, shorter prompt boxes, the output
  // contract behind a disclosure. It changes DENSITY, never membership — the config is what the
  // panel is opened for, so no width is narrow enough to fold a param away.
  compact?: boolean;
  // What each model on the menu accepts and costs, keyed by model id — the reasoning ladder
  // and the metadata card. Absent = UNKNOWN, never a menu of unsupported models.
  modelCapabilities?: Record<string, ModelCapability>;
  // values mode only: the origin's per-node permitted model sets, so an un-permitted steer is
  // disabled rather than rejected on confirm.
  permittedModels?: Record<string, readonly string[]>;
  // Prompt edits + search-space config edits ride this DraftPatch. Values-mode config
  // edits ride `onConfigChange` (the flat fork overlay). **Absence IS read-only** — there
  // is no second flag for it, so no host can claim editable while passing no callback.
  onApply?: (patch: DraftPatch) => void;
  onConfigChange?: (overlay: Record<string, Record<string, unknown>>) => void;
  // What the optimizer may MOVE from those values — a second CHANNEL on the one editor, never a
  // second panel, or the same axis gets asked twice a screen apart.
  onNarrowing?: (node: string, narrowing: NodeSearchNarrowing) => void;
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

  // A check-in AUTHORS the contract; every other host reads it. The box surfaces once `json` is
  // ticked on a node with no schema — ticking it IS asking for one — and stays to edit what the
  // draft wrote. A registry schema (`schema_family`) is the backend's: an inline one written over
  // it is ignored at parse.
  const nodeId = node?.id;
  const author = mode === "search-space" ? onApply : undefined;
  const authored = nodeId ? authoredOutputSchema(overlay, nodeId) : undefined;
  const rows = nodeId ? schema?.[nodeId] : undefined;
  const toggle = rows?.find((p) => p.key === "response_format");
  const asksForSchema = (toggle?.permitted ?? toggle?.options)?.includes("json") === true;
  const onAuthor =
    author &&
    nodeId &&
    kind === "llm" &&
    rows?.some((p) => p.key === "schema_family") !== true &&
    (authored !== undefined || (!ownOutput && asksForSchema))
      ? (next: Record<string, unknown>, answer?: string) =>
          author(nodeSchemaPatch(overlay, nodeId, next, answer))
      : undefined;
  // Locks the grid does not draw — each prompt field, each output-schema field — all flipped
  // through the grid's own emitter, so no surface can disagree with it about the rest.
  const lockKeys =
    author && nodeId
      ? (keys: readonly string[], locked: boolean) =>
          author(nodeLockPatch(schema, overlay, nodeId, keys, locked))
      : undefined;
  const ofKind = (kind: string) => (rows ?? []).filter((p) => p.kind === kind);
  const locksOf = (kind: string, prefix = "") =>
    Object.fromEntries(
      ofKind(kind).map((p) => [p.key.slice(prefix.length), p.movable_by.length === 0]),
    );
  const described = ofKind("description").map((p) => p.key);
  const tree = {
    authored,
    answer: nodeId ? authoredAnswerField(overlay, nodeId) : undefined,
    // Keyed by PATH, the tree's own address for a field.
    locks: lockKeys ? locksOf("description", DESCRIPTION_PREFIX) : undefined,
    onLock: lockKeys
      ? (path: string, locked: boolean) => lockKeys(descriptionSubtree(described, path), locked)
      : undefined,
    onAuthor,
  };

  return (
    <>
      {label ? <p className="setup-preview-sub">{label}</p> : null}

      <NodeConfigEditor
        mode={mode}
        schema={schema}
        schemaStatus={schemaStatus}
        node={node?.id}
        overlay={overlay}
        isSingleNode={isSingleNode}
        babysitEditable={babysitEditable}
        readOnly={configReadOnly}
        compact={compact}
        modelCapabilities={modelCapabilities}
        permittedModels={permittedModels}
        onApply={onApply}
        onChange={onConfigChange}
        onNarrowing={onNarrowing}
        keysAskedElsewhere={onAuthor && authored ? described : undefined}
      />

      {showPrompt ? (
        <>
          <hr className="setup-preview-divider" />
          <PromptFieldsEditor
            value={point.origin_prompt_fields}
            readOnly={readOnly}
            compact={compact}
            onApply={readOnly ? undefined : onApply}
            locks={rows ? locksOf("prompt") : undefined}
            onLock={lockKeys ? (field, locked) => lockKeys([field], locked) : undefined}
          />
        </>
      ) : null}

      {compact ? (
        <details className="node-output-fold">
          <summary>Output contract</summary>
          <OutputContract schema={nodeOutput} {...tree} />
        </details>
      ) : (
        <OutputContract schema={nodeOutput} {...tree} />
      )}
    </>
  );
}

// The structured output this node is contracted to return — every parameter, not just the
// top-level keys. In this file because the surface above renders config → prompt → output as one
// unit, and a separate component is what lets one of the three drift into a thinner reading of the
// same schema. `outputContract` flattens it; this lays the rows out.
//
// RESOLVED at this searchpoint — descriptions folded in, nothing at all where the point answers in
// text — which is why `resolved_output_schemas` exists. `never_axis` fences the OPTIMIZER off the
// schema and never the operator, so a check-in authors one here: where the host passes `onAuthor`,
// `SchemaAuthor` REPLACES this reading rather than sitting beside it, one reading of one schema.
function OutputContract({
  schema,
  onAuthor,
  ...tree
}: {
  schema: Record<string, NodeOutputSchema | null> | null;
  authored?: Record<string, unknown>;
  answer?: string;
  locks?: Record<string, boolean>;
  onLock?: (path: string, locked: boolean) => void;
  onAuthor?: (schema: Record<string, unknown>, answer?: string) => void;
}) {
  const entries = Object.entries(schema ?? {});
  const nodes = entries
    .map(([node, out]) => [node, outputContract(out)] as const)
    .filter(([, fields]) => fields.length > 0);
  // `null` is the read that has not landed — the config editor above already says so, and a second
  // line repeating it is noise. An ANSWERED read with no schema is a fact ABOUT the node, so it
  // gets a sentence: returning null there rendered "answers in free text" as nothing at all.
  if (schema === null) return null;
  if (onAuthor) return <SchemaAuthor {...tree} onAuthor={onAuthor} />;
  if (nodes.length === 0) {
    return (
      <p className="config-hint">
        {entries.length === 1
          ? "No structured output — this node answers in free text, and the matcher reads the answer out of it."
          : "No node here declares a structured output."}
      </p>
    );
  }

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

// Written in the justlogic-d234 shape: reasoning first, so it is in context before the answer.
const STARTER = {
  type: "object",
  properties: {
    reasoning: { type: "string", description: "" },
    answer: { type: "string", description: "" },
  },
  required: ["reasoning", "answer"],
  additionalProperties: false,
};

// The one place a node is GIVEN a contract: the field tree once there is one, a single button while
// there is not. A lock per field, and one on the head for the whole schema (path `""`).
function SchemaAuthor({
  authored,
  answer,
  locks,
  onLock,
  onAuthor,
}: {
  authored?: Record<string, unknown>;
  answer?: string;
  locks?: Record<string, boolean>;
  onLock?: (path: string, locked: boolean) => void;
  onAuthor: (schema: Record<string, unknown>, answer?: string) => void;
}) {
  const fields = Object.values(locks ?? {});
  const open = fields.filter((locked) => !locked).length;
  return (
    <div className="node-output-schema">
      <div className="schema-head">
        <span className="node-output-title">Structured output</span>
        {onLock && fields.length > 0 ? (
          <>
            <LockButton locked={open === 0} readOnly={false} onClick={() => onLock("", open > 0)} />
            <small className="config-hint">
              {open}/{fields.length} descriptions tunable
            </small>
          </>
        ) : null}
      </div>
      {authored ? (
        <SchemaTreeEditor
          schema={authored}
          answer={answer}
          locks={onLock ? locks : undefined}
          onLock={onLock}
          onChange={onAuthor}
        />
      ) : (
        <Button className="schema-add" onClick={() => onAuthor(STARTER)}>
          Define the structure
        </Button>
      )}
    </div>
  );
}
