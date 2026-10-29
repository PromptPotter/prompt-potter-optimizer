"use client";
import type { CapabilityMenu, DraftPatch, NodeOutputSchema } from "@/lib/api";
import type { ParamIntent } from "@/lib/api/types";
import { SCHEMA_DESCRIPTION_PREFIX } from "@/lib/api/types.generated";
import type { NodeSchemaReading } from "@/lib/types";
import type { CandidateSearchPoint, ConfigMode } from "@/lib/derivations";
import {
  authoredAnswerField,
  authoredOutputSchema,
  descriptionSubtree,
  nodeLockPatch,
  outputContract,
} from "@/lib/derivations";
import { Button } from "@/components/ui";
import type { PipelineViewNode } from "@/lib/types";
import { PromptFieldsEditor } from "./PromptFieldsEditor";
import { LockButton, NodeConfigEditor } from "./NodeConfigEditor";
import { SchemaTreeEditor } from "./SchemaTreeEditor";

export function NodeSurface({
  node,
  point,
  overlay,
  schema,
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
  // The draft's overlay (search-space) or the candidate's values — never `point.pipeline_overlay`.
  overlay: Record<string, unknown>;
  schema: NodeSchemaReading;
  label?: string;
  mode: ConfigMode;
  babysitEditable?: boolean | undefined;
  compact?: boolean | undefined;
  modelCapabilities?: CapabilityMenu | undefined;
  permittedModels?: Record<string, readonly string[]> | undefined;
  onApply?: ((patch: DraftPatch) => void) | undefined;
  onConfigChange?: ((overlay: Record<string, Record<string, unknown>>) => void) | undefined;
  onNarrowing?: ((node: string, rows: ParamIntent[]) => void) | undefined;
}) {
  const kind = node?.kind;
  // A node shows its own prompt; the whole pipeline shows each prompt node's, by its served `prompt` rows.
  const promptNodes = (schema.config ? Object.entries(schema.config) : [])
    .filter(([id]) => node === null || id === node.id)
    .map(([id, params]) => [id, params.filter((p) => p.kind === "prompt")] as const)
    .filter(([, params]) => params.length > 0);
  // Absent from the served map = no contract declared, distinct from a declared-null one.
  const ownOutput = node && schema.output ? schema.output[node.id] : undefined;
  const nodeOutput = node
    ? ownOutput === undefined
      ? null
      : { [node.id]: ownOutput }
    : schema.output;

  const readOnly = !onApply;
  const configReadOnly = mode === "search-space" ? !onApply : !onConfigChange;

  // A registry schema (`schema_family`) is the backend's: an inline one over it is ignored at parse.
  const nodeId = node?.id;
  const author = mode === "search-space" ? onApply : undefined;
  const authored = nodeId ? authoredOutputSchema(overlay, nodeId) : undefined;
  const rows = nodeId ? schema.config?.[nodeId] : undefined;
  const toggle = rows?.find((p) => p.key === "response_format");
  const asksForSchema = toggle?.permitted.includes("json") === true;
  const onAuthor =
    author &&
    nodeId &&
    kind === "llm" &&
    rows?.some((p) => p.key === "schema_family") !== true &&
    (authored !== undefined || (!ownOutput && asksForSchema))
      ? (next: Record<string, unknown>, answer?: string) =>
          author({
            node_output: { node: nodeId, output_schema: next, answer_field: answer ?? null },
          })
      : undefined;
  const lockKeys =
    author && nodeId
      ? (keys: readonly string[], locked: boolean) =>
          author(nodeLockPatch(schema.config, overlay, nodeId, keys, locked))
      : undefined;
  const ofKind = (kind: string) => (rows ?? []).filter((p) => p.kind === kind);
  const locksOf = (kind: string, prefix = "") =>
    Object.fromEntries(
      ofKind(kind).map((p) => [p.key.slice(prefix.length), p.movable_by.length === 0]),
    );
  const described = ofKind("description").map((p) => p.key);
  const tree: ContractTree = {
    authored,
    answer: nodeId ? authoredAnswerField(overlay, nodeId) : undefined,
    locks: lockKeys ? locksOf("description", SCHEMA_DESCRIPTION_PREFIX) : undefined,
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
        node={node?.id}
        overlay={overlay}
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

      {promptNodes.length > 0 ? <hr className="setup-preview-divider" /> : null}
      {promptNodes.map(([name, params]) => (
        <PromptFieldsEditor
          key={name}
          scope="target"
          node={name}
          prompts={point.origin_prompts}
          fields={params.map((p) => p.key)}
          readOnly={readOnly}
          compact={compact}
          onApply={readOnly ? undefined : onApply}
          locks={Object.fromEntries(params.map((p) => [p.key, p.movable_by.length === 0]))}
          onLock={lockKeys ? (field, locked) => lockKeys([field], locked) : undefined}
        />
      ))}

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

type AuthorSchema = (schema: Record<string, unknown>, answer?: string) => void;

interface ContractTree {
  authored: Record<string, unknown> | undefined;
  answer: string | undefined;
  locks: Record<string, boolean> | undefined;
  onLock: ((path: string, locked: boolean) => void) | undefined;
  onAuthor: AuthorSchema | undefined;
}

function OutputContract({
  schema,
  onAuthor,
  ...tree
}: {
  schema: Record<string, NodeOutputSchema | null> | null;
} & ContractTree) {
  const entries = Object.entries(schema ?? {});
  const nodes = entries
    .map(([node, out]) => [node, outputContract(out)] as const)
    .filter(([, fields]) => fields.length > 0);
  // `null` = the read has not landed, and the config editor above already says so.
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

function SchemaAuthor({
  authored,
  answer,
  locks,
  onLock,
  onAuthor,
}: Omit<ContractTree, "onAuthor"> & { onAuthor: AuthorSchema }) {
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
