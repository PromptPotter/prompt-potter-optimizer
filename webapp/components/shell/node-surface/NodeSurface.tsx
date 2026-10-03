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

// The one node surface: config → prompt → output as one unit, for ONE runnable searchpoint the
// host picks (identity props only, never a source). Draws no chrome; every host frames it.
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
  node: PipelineViewNode | null;
  point: CandidateSearchPoint;
  // The draft's optimizer overlay in search-space mode, the candidate's values in values mode —
  // never `point.pipeline_overlay`.
  overlay: Record<string, unknown>;
  // Served, never counted here. See `NodeConfigEditor`.
  isSingleNode?: boolean;
  schema: Record<string, NodeConfigParam[]> | null;
  // Travels with `schema`: an in-flight read and a node with nothing to configure are both empty.
  schemaStatus: PipelineStatus;
  outputSchema: Record<string, NodeOutputSchema | null> | null;
  label?: string;
  mode: ConfigMode;
  // values mode: gates optimizer-locked axes behind `campaign.babysit`; undefined leaves them editable.
  babysitEditable?: boolean;
  // A density, never a subset: no width folds a param away.
  compact?: boolean;
  // Absent = UNKNOWN, never "no model supports it".
  modelCapabilities?: Record<string, ModelCapability>;
  // values mode only: an un-permitted steer is disabled rather than rejected on confirm.
  permittedModels?: Record<string, readonly string[]>;
  // Absence IS read-only — there is no second flag. Values-mode config rides `onConfigChange`.
  onApply?: (patch: DraftPatch) => void;
  onConfigChange?: (overlay: Record<string, Record<string, unknown>>) => void;
  onNarrowing?: (node: string, narrowing: NodeSearchNarrowing) => void;
}) {
  const kind = node?.kind;
  const showPrompt = kind === "llm" || node === null;
  // Absent from the served map = no contract declared, distinct from a declared-null one.
  const ownOutput = node && outputSchema ? outputSchema[node.id] : undefined;
  const nodeOutput = node
    ? ownOutput === undefined
      ? null
      : { [node.id]: ownOutput }
    : outputSchema;

  const readOnly = !onApply;
  const configReadOnly = mode === "search-space" ? !onApply : !onConfigChange;

  // A check-in AUTHORS the contract. A registry schema (`schema_family`) is the backend's: an inline
  // one written over it is ignored at parse.
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
  // Locks the grid does not draw still flip through its emitter, so none can disagree with it.
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

// The output schema RESOLVED at this searchpoint. `never_axis` fences the optimizer, not the
// operator: with `onAuthor`, `SchemaAuthor` replaces this reading rather than sitting beside it.
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
  // `null` is a read not yet landed (the config editor says so); an answered read with no schema
  // is a fact about the node and gets a sentence.
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

// Reasoning first, so it is in context before the answer.
const STARTER = {
  type: "object",
  properties: {
    reasoning: { type: "string", description: "" },
    answer: { type: "string", description: "" },
  },
  required: ["reasoning", "answer"],
  additionalProperties: false,
};

// The head's lock covers the whole schema (path `""`).
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
