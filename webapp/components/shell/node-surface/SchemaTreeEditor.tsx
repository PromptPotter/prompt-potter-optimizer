"use client";
import { Button, Chip, CommitInput, ValueList } from "@/components/ui";
import { LockButton } from "./NodeConfigEditor";

// A node's output schema as an editable tree, one row per field. Emits JSON Schema only — the
// server's parser is the one validator.

type Schema = Record<string, unknown>;
type Fields = (readonly [string, Schema])[];
// Keyed by dotted field path; a list's `items` adds no segment.
type Locks = { locks?: Record<string, boolean>; onLock?: (path: string, locked: boolean) => void };

const TYPES = ["string", "number", "boolean", "object", "list"];

function asObj(v: unknown): Schema {
  return v !== null && typeof v === "object" && !Array.isArray(v) ? (v as Schema) : {};
}

function str(v: unknown): string {
  return typeof v === "string" ? v : "";
}

function fieldsOf(obj: Schema): Fields {
  return Object.entries(asObj(obj.properties)).map(([k, v]) => [k, asObj(v)] as const);
}

function typeOf(field: Schema): string {
  return field.type === "array" ? "list" : str(field.type) || "string";
}

// Every field required, nothing else allowed: what strict structured output demands.
function objectOf(obj: Schema, fields: Fields): Schema {
  return {
    ...obj,
    type: "object",
    properties: Object.fromEntries(fields),
    required: fields.map(([k]) => k),
    additionalProperties: false,
  };
}

const leaf = (type: string): Schema => ({ type, description: "" });

function retyped(field: Schema, type: string): Schema {
  const description = str(field.description);
  if (type === "list") return { type: "array", description, items: { type: "string" } };
  if (type === "object") return { ...objectOf({}, [["field", leaf("string")]]), description };
  return { type, description };
}

function freshName(taken: readonly string[]): string {
  let i = taken.length + 1;
  while (taken.includes(`field_${i}`)) i += 1;
  return `field_${i}`;
}

function branchOf(field: Schema): Schema | undefined {
  const holder = field.type === "array" ? asObj(field.items) : field;
  return holder.type === "object" ? holder : undefined;
}

function withChild(field: Schema): Schema {
  const branch = branchOf(field);
  const kids = branch ? fieldsOf(branch) : [];
  const next = objectOf(branch ?? {}, [...kids, [freshName(kids.map(([k]) => k)), leaf("string")]]);
  return field.type === "array"
    ? { ...field, items: next }
    : { ...next, description: str(field.description) };
}

export function SchemaTreeEditor({
  schema,
  answer,
  onChange,
  ...lock
}: Locks & {
  schema: Schema;
  // The top-level field graded as the answer (`answer_field`).
  answer?: string;
  onChange: (schema: Schema, answer?: string) => void;
}) {
  const names = fieldsOf(schema).map(([k]) => k);
  return (
    <>
      <FieldList
        obj={schema}
        prefix=""
        answer={answer}
        onAnswer={(field) => onChange(schema, field)}
        onChange={onChange}
        {...lock}
      />
      <Button
        variant="ghost"
        className="schema-add"
        onClick={() =>
          onChange(objectOf(schema, [...fieldsOf(schema), [freshName(names), leaf("string")]]))
        }
      >
        + field
      </Button>
    </>
  );
}

function FieldList({
  obj,
  prefix,
  answer,
  onAnswer,
  onChange,
  ...lock
}: Locks & {
  obj: Schema;
  prefix: string;
  answer?: string;
  // Top level only: `answer_field` names a top-level field.
  onAnswer?: (field: string) => void;
  onChange: (obj: Schema, answer?: string) => void;
}) {
  const fields = fieldsOf(obj);
  const names = fields.map(([k]) => k);
  const put = (i: number, entry: readonly [string, Schema]) =>
    fields.map((f, j) => (j === i ? entry : f));
  return (
    <div className="schema-fields">
      {fields.map(([name, field], i) => (
        <FieldRow
          key={name}
          name={name}
          path={prefix + name}
          field={field}
          taken={names}
          isAnswer={name === answer}
          onAnswer={onAnswer ? () => onAnswer(name) : undefined}
          onRename={(to) => onChange(objectOf(obj, put(i, [to, field])), name === answer ? to : undefined)}
          onChange={(next) => onChange(objectOf(obj, put(i, [name, next])))}
          onRemove={
            fields.length > 1
              ? () => onChange(objectOf(obj, fields.filter((_, j) => j !== i)))
              : undefined
          }
          {...lock}
        />
      ))}
    </div>
  );
}

function FieldRow({
  name,
  path,
  field,
  taken,
  isAnswer,
  onAnswer,
  onRename,
  onChange,
  onRemove,
  ...lock
}: Locks & {
  name: string;
  path: string;
  field: Schema;
  taken: readonly string[];
  isAnswer: boolean;
  onAnswer?: () => void;
  onRename: (to: string) => void;
  onChange: (field: Schema) => void;
  onRemove?: () => void;
}) {
  const type = typeOf(field);
  const list = field.type === "array";
  const branch = branchOf(field);
  const locked = lock.locks?.[path];
  return (
    <div className="schema-field">
      <div className="schema-field-row">
        <CommitInput
          className="config-input schema-field-name"
          value={name}
          aria-label="Field name"
          validate={(d) => {
            const to = d.trim();
            return to !== "" && !to.includes(".") && (to === name || !taken.includes(to));
          }}
          onCommit={(d) => {
            if (d.trim() !== name) onRename(d.trim());
          }}
        />
        <span className="schema-field-type">
          <ValueList
            name={`${name} type`}
            values={[type, ...TYPES.filter((t) => t !== type)]}
            onPick={(t) => onChange(retyped(field, t))}
          />
        </span>
        <CommitInput
          className="config-input schema-field-desc"
          value={str(field.description)}
          placeholder="What the model writes here"
          aria-label={`${name} description`}
          onCommit={(d) => onChange({ ...field, description: d })}
        />
        {lock.onLock && locked !== undefined ? (
          <LockButton locked={locked} readOnly={false} onClick={() => lock.onLock?.(path, !locked)} />
        ) : null}
        {onAnswer ? (
          <span className="schema-field-answer">
            <Chip on={isAnswer} onClick={onAnswer} title="The slot graded against the ground truth">
              answer
            </Chip>
          </span>
        ) : null}
        <Button
          variant="ghost"
          className="schema-field-add"
          aria-label={`Add a field inside ${name}`}
          title={`Add a field inside ${name}`}
          onClick={() => onChange(withChild(field))}
        >
          +
        </Button>
        {onRemove ? (
          <Button
            variant="ghost"
            className="schema-field-remove"
            aria-label={`Remove ${name}`}
            title={`Remove ${name}`}
            onClick={onRemove}
          >
            ×
          </Button>
        ) : null}
      </div>
      {branch ? (
        <FieldList
          obj={branch}
          prefix={`${path}.`}
          onChange={(obj) => onChange(list ? { ...field, items: obj } : obj)}
          {...lock}
        />
      ) : null}
    </div>
  );
}
