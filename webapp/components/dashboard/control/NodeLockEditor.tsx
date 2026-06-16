"use client";
import { useState } from "react";
import type { DraftPatch, NodeConfigParam } from "@/lib/api";
import { cx } from "@/lib/cx";
import { nodeOverlayPatch, type ParamState } from "@/lib/derivations";

// The per-node optimizer search-space editor — the generalization of the old
// `llm_only`-only AllowedValuesEditor (model 🔒 + thinking chips) to EVERY node
// and EVERY param. The operator declares what the optimizer MAY MOVE: a 🔒/🔓
// lock per param, filled/dashed/origin chips narrowing an enum's allowed set, the
// campaign-wide model lock, and an editable origin value. It writes the draft's
// `pipeline_overlay` (`nodes.{n}.{config, optimizer}`) which the backend splits
// into the per-campaign narrowing at mint (`launcher.split_overlay`).
//
// Source is the served, already-narrowed `node_config_schema` (the dataset's
// recommended default locks applied) plus the draft overlay's in-progress edits.
// `readOnly` (no `onApply`) renders the locks from the schema flags alone — the
// minted-campaign / inspection case.

function seedStates(
  params: NodeConfigParam[],
  overlayNode: Record<string, unknown> | undefined,
  lockModel: boolean,
): ParamState[] {
  const cfg = (overlayNode?.config ?? {}) as Record<string, unknown>;
  const opt = (overlayNode?.optimizer ?? {}) as {
    param_keys?: string[];
    param_allowed_values?: Record<string, string[]>;
  };
  const overlayKeys = opt.param_keys;
  const overlayAllowed = opt.param_allowed_values ?? {};
  return params.map((p) => {
    const isModel = p.kind === "model";
    const baseValue = p.value == null ? "" : String(p.value);
    const locked = isModel
      ? lockModel
      : overlayKeys !== undefined
        ? !overlayKeys.includes(p.key)
        : !p.optimizer_tunable;
    return {
      key: p.key,
      kind: p.kind,
      locked,
      allowed: p.key in overlayAllowed ? overlayAllowed[p.key] : p.options,
      options: p.options,
      value: p.key in cfg ? String(cfg[p.key]) : baseValue,
      baseValue,
    };
  });
}

export function NodeLockEditor({
  node,
  params,
  overlayBase,
  lockModel: lockModelProp,
  onApply,
  readOnly = false,
}: {
  node: string;
  params: NodeConfigParam[];
  overlayBase: Record<string, unknown>;
  lockModel: boolean;
  onApply?: (patch: DraftPatch) => void;
  readOnly?: boolean;
}) {
  const overlayNode = overlayBase[node] as Record<string, unknown> | undefined;
  const [prevNode, setPrevNode] = useState(node);
  const [lockModel, setLockModel] = useState(lockModelProp);
  const [states, setStates] = useState<ParamState[]>(() =>
    seedStates(params, overlayNode, lockModelProp),
  );
  // Render-phase guarded reset — reseed when the viewed node changes (the editor
  // is reused across node selections). Local state is the source of truth during
  // an edit; we don't reseed on overlayBase round-trips.
  if (node !== prevNode) {
    setPrevNode(node);
    setLockModel(lockModelProp);
    setStates(seedStates(params, overlayNode, lockModelProp));
  }

  if (params.length === 0) {
    return (
      <div className="config-editor">
        <small className="config-hint">This node declares no configurable params.</small>
      </div>
    );
  }

  const persist = (nextStates: ParamState[], nextLockModel: boolean) => {
    if (!onApply) return;
    onApply(nodeOverlayPatch(overlayBase, nextLockModel, node, nextStates));
  };

  const update = (i: number, patch: Partial<ParamState>) => {
    const next = states.map((s, j) => (j === i ? { ...s, ...patch } : s));
    setStates(next);
    persist(next, lockModel);
  };

  const toggleModelLock = () => {
    const v = !lockModel;
    setLockModel(v);
    setStates((prev) => prev.map((s) => (s.kind === "model" ? { ...s, locked: v } : s)));
    persist(states, v);
  };

  // Node-level master lock: lock every non-model param at once (or open them).
  // Reflects "locked" when no non-model param is currently open.
  const movable = states.filter((s) => s.kind !== "model");
  const nodeLocked = movable.length > 0 && movable.every((s) => s.locked);
  const toggleNodeLock = () => {
    const v = !nodeLocked;
    const next = states.map((s) => (s.kind === "model" ? s : { ...s, locked: v }));
    setStates(next);
    persist(next, lockModel);
  };

  const toggleChip = (i: number, level: string) => {
    const s = states[i];
    const on = s.allowed.includes(level);
    const next = on ? s.allowed.filter((l) => l !== level) : [...s.allowed, level];
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

      {states.map((s, i) =>
        s.kind === "model" ? (
          <ParamModelRow
            key={s.key}
            state={s}
            readOnly={readOnly}
            onToggleLock={toggleModelLock}
            onValue={(v) => update(i, { value: v })}
          />
        ) : s.kind === "enum" ? (
          <ParamEnumRow
            key={s.key}
            state={s}
            readOnly={readOnly}
            onToggleLock={() => update(i, { locked: !s.locked })}
            onToggleChip={(level) => toggleChip(i, level)}
          />
        ) : (
          <ParamValueRow
            key={s.key}
            state={s}
            readOnly={readOnly}
            onToggleLock={() => update(i, { locked: !s.locked })}
            onValue={(v) => update(i, { value: v })}
          />
        ),
      )}

      <small className="config-hint">
        🔒 = origin-locked (the optimizer keeps it fixed) · 🔓 = the optimizer may tune it · filled
        chip = allowed value · dashed = locked out · “origin” = the starting value.
      </small>
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

function ParamModelRow({
  state,
  readOnly,
  onToggleLock,
  onValue,
}: {
  state: ParamState;
  readOnly: boolean;
  onToggleLock: () => void;
  onValue: (v: string) => void;
}) {
  return (
    <div className="config-row">
      <span className="config-label">{state.key}</span>
      <span className="config-value config-model">
        <LockButton locked={state.locked} readOnly={readOnly} onClick={onToggleLock} />
        <select
          className="config-input"
          value={state.value}
          disabled={readOnly}
          aria-label="Model"
          onChange={(e) => onValue(e.target.value)}
        >
          {!state.options.includes(state.value) && state.value !== "" ? (
            <option value={state.value}>{state.value} (current)</option>
          ) : null}
          {state.options.map((m) => (
            <option key={m} value={m}>
              {m}
            </option>
          ))}
        </select>
      </span>
    </div>
  );
}

function ParamEnumRow({
  state,
  readOnly,
  onToggleLock,
  onToggleChip,
}: {
  state: ParamState;
  readOnly: boolean;
  onToggleLock: () => void;
  onToggleChip: (level: string) => void;
}) {
  return (
    <div className="config-row">
      <span className="config-label">{state.key}</span>
      <span className="config-value">
        <LockButton locked={state.locked} readOnly={readOnly} onClick={onToggleLock} />
        {state.options.map((level) => {
          const on = state.allowed.includes(level);
          const isOrigin = level === state.baseValue;
          return (
            <button
              key={level}
              type="button"
              className={cx(
                "config-level",
                on ? "is-on" : "is-off",
                isOrigin && "is-origin",
                state.locked && "is-disabled",
              )}
              onClick={() => onToggleChip(level)}
              disabled={readOnly || state.locked}
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
        })}
      </span>
    </div>
  );
}

function ParamValueRow({
  state,
  readOnly,
  onToggleLock,
  onValue,
}: {
  state: ParamState;
  readOnly: boolean;
  onToggleLock: () => void;
  onValue: (v: string) => void;
}) {
  return (
    <div className="config-row">
      <span className="config-label">{state.key}</span>
      <span className="config-value">
        <LockButton locked={state.locked} readOnly={readOnly} onClick={onToggleLock} />
        {state.kind === "bool" ? (
          <input
            type="checkbox"
            className="config-check"
            checked={state.value === "true"}
            disabled={readOnly}
            aria-label={state.key}
            onChange={(e) => onValue(e.target.checked ? "true" : "false")}
          />
        ) : (
          <input
            type={state.kind === "number" ? "number" : "text"}
            inputMode={state.kind === "number" ? "decimal" : undefined}
            className="config-input"
            value={state.value}
            disabled={readOnly}
            placeholder={state.kind === "number" ? "inherit" : undefined}
            aria-label={state.key}
            onChange={(e) => onValue(e.target.value)}
          />
        )}
      </span>
    </div>
  );
}
