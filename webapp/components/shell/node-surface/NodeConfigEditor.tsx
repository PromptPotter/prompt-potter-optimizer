"use client";
import { useMemo, useState } from "react";
import type { DraftPatch, ModelCapability, NodeConfigParam } from "@/lib/api";
import type { NodeSearchNarrowing } from "@/lib/api/types";
import { CommitInput, ValueList } from "@/components/ui";
import { cx } from "@/lib/cx";
import {
  agentLabel,
  configRows,
  effortLadder,
  flatConfigKey,
  parseNested,
  nodeNarrowing,
  nodeOverlayPatch,
  seedOverlayFromRows,
  type ConfigMode,
  type ConfigRow,
} from "@/lib/derivations";
import type { PipelineStatus } from "@/lib/types";

// The one node-config editor for every host; `mode` picks only the value transport. Governed by
// `webapp/CLAUDE.md` § Component conventions (an axis is a `ValueList`; gate on the fact, not a callback).
export function NodeConfigEditor(props: {
  mode: ConfigMode;
  schema: Record<string, NodeConfigParam[]> | null;
  // Not read off the connector context: two hosts resolve `schema` from a different read, and the
  // context's status would describe someone else's fetch.
  schemaStatus: PipelineStatus;
  // search-space merges a patch onto it and reads no row from it; values seeds its rows from it.
  overlay: Record<string, unknown>;
  // SERVED, never counted: rows cover every DECLARED node, while the active chain may run one.
  isSingleNode?: boolean;
  node?: string;
  readOnly?: boolean;
  // values mode: false holds un-permitted models read-only — steering to one is the ADR-0005
  // babysit act, needing `campaign.babysit`.
  babysitEditable?: boolean;
  compact?: boolean;
  // Absent = UNKNOWN, never "no model supports it".
  modelCapabilities?: Record<string, ModelCapability>;
  // values mode only: an un-permitted steer is disabled rather than rejected on confirm.
  permittedModels?: Record<string, readonly string[]>;
  onApply?: (patch: DraftPatch) => void;
  // search-space only: the permission half alone, per NODE — the editor can span the whole pipeline.
  onNarrowing?: (node: string, narrowing: NodeSearchNarrowing) => void;
  onChange?: (overlay: Record<string, Record<string, unknown>>) => void;
  // Not drawn here but still in every emit: a row missing from `rows` would leave `param_keys`,
  // reading as the operator closing it.
  keysAskedElsewhere?: readonly string[];
}) {
  const {
    mode,
    schema,
    schemaStatus,
    overlay,
    isSingleNode = false,
    node,
    readOnly = false,
    babysitEditable = true,
    compact = false,
    modelCapabilities,
    permittedModels,
    onApply,
    onNarrowing,
    onChange,
    keysAskedElsewhere,
  } = props;
  const nodeId = node ?? "";
  // Kept beside the edited copy: `seedOverlayFromRows` needs the untouched seed to tell an edit
  // from an inherited value.
  const base = useMemo(
    () => configRows(schema, overlay, mode, node),
    [schema, overlay, mode, node],
  );
  const [rows, setRows] = useState<ConfigRow[]>(base);
  const [touched, setTouched] = useState<ReadonlySet<string>>(() => new Set());
  // The steer fork's seed lands async (`useRoundFile`); an earlier edit must not mask it.
  const [prevBase, setPrevBase] = useState(base);
  if (base !== prevBase) {
    setPrevBase(base);
    setRows(base);
    setTouched(new Set());
  }

  const drawn = (r: ConfigRow) => r.kind !== "prompt" && !keysAskedElsewhere?.includes(r.key);
  if (!rows.some(drawn)) {
    return <EmptyConfig status={schemaStatus} schema={schema} node={node} />;
  }

  const persist = (next: ConfigRow[], marks: ReadonlySet<string>) => {
    onApply?.(nodeOverlayPatch(overlay, nodeId, next));
    for (const n of new Set(next.map((r) => r.node))) {
      onNarrowing?.(n, nodeNarrowing(next.filter((r) => r.node === n)));
    }
    onChange?.(
      seedOverlayFromRows(
        base,
        Object.fromEntries(
          next
            .filter((r) => marks.has(flatConfigKey(r.node, r.key)))
            .map((r) => [flatConfigKey(r.node, r.key), r.value]),
        ),
      ),
    );
  };
  const update = (i: number, patch: Partial<ConfigRow>) => {
    const next = rows.map((r, j) => (j === i ? { ...r, ...patch } : r));
    const marks =
      patch.value === undefined
        ? touched
        : new Set([...touched, flatConfigKey(next[i]!.node, next[i]!.key)]);
    setRows(next);
    setTouched(marks);
    persist(next, marks);
  };

  const narrowChannel = Boolean(onApply || onNarrowing);
  const canSetValue = Boolean(onApply || onChange);

  const lockable = (r: ConfigRow) =>
    narrowChannel && drawn(r) && r.kind !== "model" && r.kind !== "enum" && !r.neverAxis;
  const freeValued = rows.filter(lockable);
  const nodeLocked = freeValued.length > 0 && freeValued.every((r) => r.locked);
  const toggleNodeLock = () => {
    const next = rows.map((r) => (lockable(r) ? { ...r, locked: !nodeLocked } : r));
    setRows(next);
    persist(next, touched);
  };

  const toggle = (i: number, value: string) => {
    const r = rows[i];
    if (!r) return;
    const on = r.allowed.includes(value);
    const next = on ? r.allowed.filter((v) => v !== value) : [...r.allowed, value];
    if (next.length === 0) return; // an axis with nothing permitted has nothing to run
    update(i, { allowed: next });
  };
  // A widening rides the PERMITTED set: `PipelineSchema.narrow` replaces rather than intersects it,
  // so an added value survives the mint on a reused dataset.
  const add = (i: number, value: string) => {
    const r = rows[i];
    if (!r || r.allowed.includes(value)) return;
    update(i, { allowed: [...r.allowed, value] });
  };

  // The picked model qualifies the reasoning ladder on the MENU only; the ticks stay the campaign's.
  const pickedModel = rows.find((r) => r.kind === "model")?.value ?? "";
  const caps = modelCapabilities?.[pickedModel];

  return (
    <div className={cx("config-editor", compact && "is-compact")}>
      {rows.map((r, i) => {
        if (!drawn(r)) return null;
        if (r.kind !== "model" && r.kind !== "enum") {
          return (
            <ConfigRowView
              key={`${r.node}.${r.key}`}
              row={r}
              // `unsupported_params` absent = the catalogue said nothing, and the row claims nothing.
              ignoredBy={caps?.unsupported_params?.includes(r.key) ? pickedModel : undefined}
              readOnly={readOnly || (!babysitEditable && r.neverAxis === "cost_lever")}
              onToggleLock={lockable(r) ? () => update(i, { locked: !r.locked }) : undefined}
              onValue={canSetValue ? (v) => update(i, { value: v }) : undefined}
            />
          );
        }
        const { values, inert, userAdded } = axisMenu(r, caps);
        // Without the babysit cap, un-permitted models ride `inert`, like a capability refusal.
        const barred =
          r.kind === "model" && !babysitEditable
            ? values.filter((v) => !(permittedModels?.[r.node] ?? []).includes(v))
            : [];
        return (
          <div key={`${r.node}.${r.key}`} className="config-row">
            <span className="config-label" title={r.description || undefined}>
              {r.key}
              <EvolvedMark row={r} />
            </span>
            {/* A div: `ValueList` opens a `Popover`, which is flow content. */}
            <div className="config-value">
              <ValueList
                name={r.key}
                values={values}
                checked={narrowChannel ? r.allowed : undefined}
                inert={[...inert, ...barred]}
                userAdded={userAdded}
                note={axisNote(r, caps, pickedModel)}
                readOnly={readOnly}
                addPlaceholder={r.kind === "model" ? "another model id…" : "another value…"}
                onPick={canSetValue ? (v) => update(i, { value: v }) : undefined}
                onToggle={narrowChannel ? (v) => toggle(i, v) : undefined}
                onAdd={narrowChannel ? (v) => add(i, v) : undefined}
              />
              {r.kind === "model" && caps ? <ModelCard caps={caps} /> : null}
            </div>
          </div>
        );
      })}

      {!isSingleNode && freeValued.length > 0 ? (
        <div className="config-row config-node-row">
          <span className="config-label">Tuning</span>
          <button
            type="button"
            className={cx("config-lock", nodeLocked && "is-locked")}
            onClick={toggleNodeLock}
            disabled={readOnly}
            aria-pressed={nodeLocked}
            title={
              nodeLocked
                ? "Every free-valued param on this node is held at its origin value. Click to let the optimizer tune them."
                : "The optimizer may tune the unlocked params above. Click to hold them at origin."
            }
          >
            {nodeLocked ? "🔒 Params locked" : "🔓 Params open"}
          </button>
        </div>
      ) : null}
      {narrowChannel ? (
        <small className="config-hint">
          Click an axis to drop its list. The first value is where this point starts — click
          another to move it there; ☑ = what the optimizer may pick, and one value left pins the
          axis. 🔒 / 🔓 = held / tunable, for the params that carry no value list.
        </small>
      ) : null}
    </div>
  );
}

// No rows is several facts; `frontend-surface-contract.md::I1` — never one wearing another's words.
function EmptyConfig({
  status,
  schema,
  node,
}: {
  status: PipelineStatus;
  schema: Record<string, NodeConfigParam[]> | null;
  node?: string;
}) {
  // Whole-pipeline hosts pass no node, so this reads the flattened schema — else a prompt-only
  // pipeline would read as declaring nothing.
  const scoped = node !== undefined && schema !== null ? schema[node] : undefined;
  const declared = node !== undefined ? scoped : Object.values(schema ?? {}).flat();
  const subject = node !== undefined ? "node" : "pipeline";
  const said =
    status === "loading"
      ? "Resolving what this campaign runs…"
      : status === "error"
        ? "The campaign's pipeline could not be read, so what this node runs is unknown — not empty."
        : status === "unbound"
          ? "No campaign bound, so there is no resolved pipeline to read this node from."
          : node !== undefined && schema !== null && scoped === undefined
            ? `The served pipeline declares no node called ${node}.`
            : declared && declared.length > 0
              ? `Every param on this ${subject} is a prompt field — the prompt editor below is where they are.`
              : `This ${subject} declares no params.`;
  return (
    <div className="config-editor">
      <small className="config-hint">{said}</small>
    </div>
  );
}

// `inert` strikes only where the model answered — UNKNOWN never strikes. Ticked AND struck stay two
// facts: folding them would emit the model's refusals as the campaign's own narrowing.
function axisMenu(row: ConfigRow, caps: ModelCapability | undefined) {
  const ladder = effortLadder(row, caps);
  const rest = [...new Set([...ladder, ...row.allowed])].filter((v) => v !== row.value);
  const values = row.value ? [row.value, ...rest] : rest;
  const offered = new Set([...row.options, ...ladder]);
  const modelAnswered = row.key === "reasoning_effort" && caps?.reasoning_efforts != null;
  return {
    values,
    inert: modelAnswered ? values.filter((v) => !ladder.includes(v)) : [],
    userAdded: values.filter((v) => !offered.has(v)),
  };
}

function axisNote(
  row: ConfigRow,
  caps: ModelCapability | undefined,
  pickedModel: string,
): string {
  const grant =
    row.movableBy.length > 0
      ? `Searched by ${row.movableBy.map(agentLabel).join(", ")} — ticks are what it may pick.`
      : "Nothing searches this axis today — ticks sanction a human fork.";
  if (row.key !== "reasoning_effort" || !pickedModel || !caps) return grant;
  return `${grant} ${pickedModel}: ${caps.reasoning_note}`;
}

function ModelCard({ caps }: { caps: ModelCapability }) {
  const tok = (v: number | null) => (v === null ? null : `${v.toLocaleString()} tok`);
  const usd = (v: number | null) => (v === null ? "?" : `$${v.toFixed(2)}`);
  const price =
    caps.input_usd_per_mtok === null && caps.output_usd_per_mtok === null
      ? null
      : `${usd(caps.input_usd_per_mtok)} in / ${usd(caps.output_usd_per_mtok)} out per Mtok`;
  const facts: [string, string | null][] = [
    ["context", tok(caps.context_length)],
    ["max out", tok(caps.max_output_tokens)],
    ["price", price],
    ["modality", caps.modality || null],
    ["source", caps.fetched_at ? `${caps.source} · ${caps.fetched_at.slice(0, 10)}` : caps.source],
  ];
  const shown = facts.filter((f): f is [string, string] => f[1] !== null);
  if (shown.length === 0) return null;
  return (
    <dl className="model-card">
      {caps.display_name ? <div className="model-card-name">{caps.display_name}</div> : null}
      {shown.map(([k, v]) => (
        <div key={k} className="model-card-row">
          <dt>{k}</dt>
          <dd>{v}</dd>
        </div>
      ))}
    </dl>
  );
}

// The served `source`, never seed membership: a steered fork's seed writes every key.
function EvolvedMark({ row }: { row: ConfigRow }) {
  return row.source === "evolved" ? (
    <span className="config-evolved" title="Set by this searchpoint's own mutation">
      ·evolved
    </span>
  ) : null;
}

function ConfigRowView({
  row,
  readOnly,
  ignoredBy,
  onToggleLock,
  onValue,
}: {
  row: ConfigRow;
  readOnly: boolean;
  ignoredBy?: string;
  onToggleLock?: () => void;
  onValue?: (v: string) => void;
}) {
  return (
    <div className="config-row">
      <span className="config-label">
        {row.key}
        <EvolvedMark row={row} />
        {ignoredBy ? (
          <span
            className="config-optlocked"
            title={`${ignoredBy} does not accept ${row.key} — it is dropped by the provider, so this value has no effect.`}
          >
            ⊘
          </span>
        ) : null}
        {onToggleLock ? (
          <LockButton locked={row.locked} readOnly={readOnly} onClick={onToggleLock} />
        ) : row.movableBy.length > 0 ? (
          <span
            className="config-optmovable"
            title={`Searched by ${row.movableBy.map(agentLabel).join(", ")}.`}
          >
            🔓
          </span>
        ) : (
          <span className="config-optlocked" title={lockReason(row, readOnly)}>
            🔒
          </span>
        )}
      </span>
      <div className="config-value">
        {!onValue ? (
          // Text, not a disabled input: a greyed box says "you may not" where the truth is "not here".
          <span className={cx("config-static", row.kind === "nested" && "is-structured")}>
            {row.value || "—"}
          </span>
        ) : row.kind === "nested" ? (
          // `parseNested` is the emitter's own question, so the box cannot accept what it would drop.
          <CommitInput
            rows={6}
            validate={(d) => parseNested(d) !== undefined}
            className="config-input is-structured"
            value={row.value}
            disabled={readOnly}
            placeholder="inherit"
            aria-label={row.key}
            onCommit={onValue}
          />
        ) : row.kind === "bool" ? (
          <input
            type="checkbox"
            className="config-check"
            checked={row.value === "true"}
            disabled={readOnly}
            aria-label={row.key}
            onChange={(e) => onValue(e.target.checked ? "true" : "false")}
          />
        ) : (
          // Never per keystroke: each emission invalidates a searchpoint and its descendants on Compare.
          <CommitInput
            type={row.kind === "number" ? "number" : "text"}
            inputMode={row.kind === "number" ? "decimal" : undefined}
            className="config-input"
            value={row.value}
            disabled={readOnly}
            placeholder={row.kind === "number" ? "inherit" : undefined}
            aria-label={row.key}
            onCommit={onValue}
          />
        )}
      </div>
    </div>
  );
}

// `neverAxis` outranks `held`. Both `neverAxis` reasons are SERVED — never tell them apart by key name.
function lockReason(row: ConfigRow, readOnly: boolean): string {
  if (row.neverAxis === "schema_owned") {
    // `never_axis` says who may SEARCH a key (`SCHEMA_OWNED_FIELDS`), never who may set it.
    return "The structured-output contract — the shape this node answers in, and which slot carries the answer. No optimizer may search it; set it here to steer a fork onto a different contract.";
  }
  if (row.neverAxis === "cost_lever") {
    const held =
      "Never a search axis — the gateway and the route are cost levers set against a measured capture.";
    return readOnly
      ? `${held} Locked on this fork.`
      : `${held} You can still set it on this fork (a babysit edit).`;
  }
  if (row.held) {
    return "The dataset offers this as a search axis and this campaign closed it at mint. Reopen it on a fork.";
  }
  return "Nothing searches this. It COULD be opened: an axis is a key in the node's `param_keys`. Open it on a fork.";
}

export function LockButton({
  locked,
  readOnly,
  onClick,
}: {
  locked: boolean;
  readOnly: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      className={cx("config-lock", locked && "is-locked")}
      onClick={onClick}
      disabled={readOnly}
      aria-pressed={locked}
      title={
        locked
          ? "Origin-locked — the optimizer keeps this fixed. Click to let it tune."
          : "Open — the optimizer may tune this. Click to lock at origin."
      }
    >
      {locked ? "🔒" : "🔓"}
    </button>
  );
}
