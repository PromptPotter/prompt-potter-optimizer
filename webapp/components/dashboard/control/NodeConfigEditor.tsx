"use client";
import { useMemo, useState } from "react";
import type { DraftPatch, NodeConfigParam } from "@/lib/api";
import { cx } from "@/lib/cx";
import {
  configRows,
  nodeOverlayPatch,
  seedOverlayFromRows,
  type ConfigMode,
  type ConfigRow,
} from "@/lib/derivations";

// The one node-config editor, mode-driven:
//   - "search-space" (before mint): the operator declares what the optimizer MAY
//     MOVE — a 🔒/🔓 lock per param, enum allow/deny chips, the campaign-wide model
//     lock, a node-level master lock, an editable origin value. Per-node (`node`
//     scopes it). Persists the draft `pipeline_overlay` via `onApply`.
//   - "values" (steer / inspect a fork): the concrete `{node:{param:value}}` the
//     fork is seeded with. Whole-pipeline. Emits the sparse overlay via `onChange`.
// Both share one row renderer; only the enum row (allow/deny chips vs a single
// <select>) and the lock/badge chrome differ. `readOnly` disables the inputs.
export function NodeConfigEditor(props: {
  mode: ConfigMode;
  schema: Record<string, NodeConfigParam[]> | null;
  seedOverlay: Record<string, unknown>;
  node?: string;
  lockModel?: boolean;
  readOnly?: boolean;
  onApply?: (patch: DraftPatch) => void;
  onChange?: (overlay: Record<string, Record<string, unknown>>) => void;
}) {
  return props.mode === "search-space" ? (
    <SearchSpaceEditor {...props} />
  ) : (
    <ValuesEditor {...props} />
  );
}

function EmptyConfig() {
  return (
    <div className="config-editor">
      <small className="config-hint">This node declares no configurable params.</small>
    </div>
  );
}

// search-space: lock/allow per param, model lock, node master-lock, editable
// origin value. Local rows are the source of truth during an edit; reseed (the
// render-phase guarded reset) only when the viewed node changes.
function SearchSpaceEditor({
  schema,
  seedOverlay,
  node,
  lockModel: lockModelProp = false,
  readOnly = false,
  onApply,
}: {
  schema: Record<string, NodeConfigParam[]> | null;
  seedOverlay: Record<string, unknown>;
  node?: string;
  lockModel?: boolean;
  readOnly?: boolean;
  onApply?: (patch: DraftPatch) => void;
}) {
  const nodeId = node ?? "";
  const [prevNode, setPrevNode] = useState(nodeId);
  const [lockModel, setLockModel] = useState(lockModelProp);
  const [rows, setRows] = useState<ConfigRow[]>(() =>
    configRows(schema, seedOverlay, "search-space", nodeId, lockModelProp),
  );
  if (nodeId !== prevNode) {
    setPrevNode(nodeId);
    setLockModel(lockModelProp);
    setRows(configRows(schema, seedOverlay, "search-space", nodeId, lockModelProp));
  }

  if (rows.length === 0) return <EmptyConfig />;

  const persist = (next: ConfigRow[], nextLock: boolean) => {
    if (!onApply) return;
    onApply(nodeOverlayPatch(seedOverlay, nextLock, nodeId, next));
  };
  const update = (i: number, patch: Partial<ConfigRow>) => {
    const next = rows.map((r, j) => (j === i ? { ...r, ...patch } : r));
    setRows(next);
    persist(next, lockModel);
  };
  const toggleModelLock = () => {
    const v = !lockModel;
    setLockModel(v);
    const next = rows.map((r) => (r.kind === "model" ? { ...r, locked: v } : r));
    setRows(next);
    persist(next, v);
  };

  // Node-level master lock: lock every non-model param at once (or open them);
  // reflects "locked" when no non-model param is currently open.
  const movable = rows.filter((r) => r.kind !== "model");
  const nodeLocked = movable.length > 0 && movable.every((r) => r.locked);
  const toggleNodeLock = () => {
    const v = !nodeLocked;
    const next = rows.map((r) => (r.kind === "model" ? r : { ...r, locked: v }));
    setRows(next);
    persist(next, lockModel);
  };
  const toggleChip = (i: number, level: string) => {
    const r = rows[i];
    const on = r.allowed.includes(level);
    const next = on ? r.allowed.filter((l) => l !== level) : [...r.allowed, level];
    if (next.length === 0) return; // keep at least one value allowed
    update(i, { allowed: next });
  };

  return (
    <div className="config-editor">
      {movable.length > 0 ? (
        <div className="config-row config-node-row">
          <span className="config-label">Optimizer</span>
          <button
            type="button"
            className={cx("config-lock", nodeLocked && "is-locked")}
            onClick={toggleNodeLock}
            disabled={readOnly}
            aria-pressed={nodeLocked}
            title={
              nodeLocked
                ? "This node is origin-locked — the optimizer keeps it fixed. Click to let it tune the params below."
                : "The optimizer may tune the unlocked params below. Click to lock the whole node at origin."
            }
          >
            {nodeLocked ? "🔒 Node locked" : "🔓 Node open"}
          </button>
        </div>
      ) : null}

      {rows.map((r, i) => (
        <ConfigRowView
          key={r.key}
          row={r}
          mode="search-space"
          readOnly={readOnly}
          onToggleLock={() => (r.kind === "model" ? toggleModelLock() : update(i, { locked: !r.locked }))}
          onToggleChip={(level) => toggleChip(i, level)}
          onValue={(v) => update(i, { value: v })}
        />
      ))}

      <small className="config-hint">
        🔒 = origin-locked (the optimizer keeps it fixed) · 🔓 = the optimizer may tune it · filled
        chip = allowed value · dashed = locked out · “origin” = the starting value.
      </small>
    </div>
  );
}

// values: concrete fork values. Rows are pure-derived from schema + seed overlay
// (both stable for the life of a steer); an `edits` string-map overlays operator
// changes, and the sparse overlay is emitted on every edit. `node` scopes the rows
// to one node (the OBSERVE-run drill-in shows the clicked node's config); omit for
// the whole-pipeline seed (draft preview, steer fork). Symmetric with SearchSpaceEditor.
function ValuesEditor({
  schema,
  seedOverlay,
  node,
  readOnly = false,
  onChange,
}: {
  schema: Record<string, NodeConfigParam[]> | null;
  seedOverlay: Record<string, unknown>;
  node?: string;
  readOnly?: boolean;
  onChange?: (overlay: Record<string, Record<string, unknown>>) => void;
}) {
  const rows = useMemo(
    () => configRows(schema, seedOverlay, "values", node),
    [schema, seedOverlay, node],
  );
  const [edits, setEdits] = useState<Record<string, string>>({});

  if (rows.length === 0) return <EmptyConfig />;

  const set = (key: string, v: string) => {
    const next = { ...edits, [key]: v };
    setEdits(next);
    onChange?.(seedOverlayFromRows(rows, next));
  };

  return (
    <div className="config-editor">
      {rows.map((r) => {
        const key = `${r.node}.${r.key}`;
        const value = key in edits ? edits[key] : r.value;
        return (
          <ConfigRowView
            key={key}
            row={{ ...r, value }}
            mode="values"
            readOnly={readOnly}
            onValue={(v) => set(key, v)}
          />
        );
      })}
    </div>
  );
}

// One row, shared by both modes. The lock button + enum allow/deny chips +
// `origin` tag are search-space only; the `·evolved` / `optimizer-locked` key
// badges are values only. Everything else (model select, number/text/bool
// inputs, the "keep current value selectable" guard) is shared.
function ConfigRowView({
  row,
  mode,
  readOnly,
  onToggleLock,
  onToggleChip,
  onValue,
}: {
  row: ConfigRow;
  mode: ConfigMode;
  readOnly: boolean;
  onToggleLock?: () => void;
  onToggleChip?: (level: string) => void;
  onValue: (v: string) => void;
}) {
  const isSearch = mode === "search-space";
  const isModel = row.kind === "model";
  return (
    <div className="config-row">
      <span className="config-label">
        {row.key}
        {!isSearch && row.fromCandidate ? (
          <span className="config-evolved" title="Carried from this searchpoint">
            ·evolved
          </span>
        ) : null}
        {!isSearch && row.optimizerLocked ? (
          <span
            className="config-optlocked"
            title="The optimizer can't change this — but you can, on this fork."
          >
            optimizer-locked
          </span>
        ) : null}
      </span>
      <span className={cx("config-value", isModel && "config-model")}>
        {isSearch ? (
          <LockButton locked={row.locked} readOnly={readOnly} onClick={onToggleLock!} />
        ) : null}
        {isModel || (row.kind === "enum" && !isSearch) ? (
          <select
            className="config-input"
            value={row.value}
            disabled={readOnly}
            aria-label={isModel ? "Model" : row.key}
            onChange={(e) => onValue(e.target.value)}
          >
            {/* The value may sit outside the declared set (the candidate evolved
                past it) — keep it selectable so the editor never rewrites it. */}
            {!row.options.includes(row.value) && row.value !== "" ? (
              <option value={row.value}>{row.value} (current)</option>
            ) : null}
            {row.options.map((m) => (
              <option key={m} value={m}>
                {m}
              </option>
            ))}
          </select>
        ) : row.kind === "enum" ? (
          row.options.map((level) => {
            const on = row.allowed.includes(level);
            const isOrigin = level === row.baseValue;
            return (
              <button
                key={level}
                type="button"
                className={cx(
                  "config-level",
                  on ? "is-on" : "is-off",
                  isOrigin && "is-origin",
                  row.locked && "is-disabled",
                )}
                onClick={() => onToggleChip?.(level)}
                disabled={readOnly || row.locked}
                aria-pressed={on}
                title={
                  isOrigin
                    ? "Origin value — click to allow / lock out"
                    : on
                      ? "Allowed — click to lock the optimizer out of this value"
                      : "Locked out — click to allow"
                }
              >
                {level}
                {isOrigin ? <span className="config-origin-tag">origin</span> : null}
              </button>
            );
          })
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
          <input
            type={row.kind === "number" ? "number" : "text"}
            inputMode={row.kind === "number" ? "decimal" : undefined}
            className="config-input"
            value={row.value}
            disabled={readOnly}
            placeholder={row.kind === "number" ? "inherit" : undefined}
            aria-label={row.key}
            onChange={(e) => onValue(e.target.value)}
          />
        )}
      </span>
    </div>
  );
}

function LockButton({
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
