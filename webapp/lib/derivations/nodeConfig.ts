// The one node-config model + seed + the two overlay-emit paths. A single row
// type (`ConfigRow`) and one seed function (`configRows`) back the mode-driven
// `NodeConfigEditor`; the two emit functions stay distinct because they ride two
// different backend transports:
//
//   - search-space → `nodeOverlayPatch` → the draft `pipeline_overlay`
//     (`nodes.{n}.{config, optimizer}`); at mint the backend splits it into the
//     per-campaign `pipeline_overrides` (config values) + `optimizer_narrowing`
//     (the param-lock / allowed-values subset). See `launcher.split_overlay`.
//   - values → `seedOverlayFromRows` → the flat `{node:{param:value}}` overlay
//     that `OperatorForkOverride.pipeline_overlay` merges onto
//     `session.pipeline_params` at fork bootstrap.
//
// Pure data→data (no React, no I/O) so it rides the lib Vitest scope.

import type { DraftPatch, NodeConfigParam } from "@/lib/api";

export type ConfigMode = "search-space" | "values";

// One row of the config editor. Search-space fields (`locked`, `allowed`,
// `baseValue`) drive the optimizer search-space lever; values fields
// (`fromCandidate`, `optimizerLocked`) drive the concrete fork-value lever. A row
// carries both; the active `mode` decides which it renders + emits.
export interface ConfigRow {
  node: string;
  key: string;
  // "model" | "enum" | "number" | "bool" | "string" — drives the widget +
  // coercion (enums carry a narrowed allow-set).
  kind: string;
  // Full declared value set (model/enum); empty otherwise.
  options: string[];
  // Current (editable) value, stringified for the input. "" when neither the
  // overlay nor the schema carries it (declared but unset, e.g. max_tokens).
  value: string;
  // The schema's value (origin). A change vs this lands the param in `config`
  // (search-space); also the enum's "origin" chip tag.
  baseValue: string;
  // search-space: the optimizer may NOT move this param (leaves `param_keys`).
  // Always true for `model`/`provider` — they are never an optimizer axis.
  locked: boolean;
  // search-space: the allowed-values subset the optimizer may use when open.
  allowed: string[];
  // values: present in the candidate overlay (vs the config floor) — keep it in
  // the emitted overlay even when the operator leaves it untouched.
  fromCandidate: boolean;
  // The optimizer can't permute this (model/provider — always locked). Shown as a
  // hint; a cap-holding operator may still set it on a fork (a babysit edit).
  optimizerLocked: boolean;
  description: string;
}

function asObj(v: unknown): Record<string, unknown> {
  return v && typeof v === "object" && !Array.isArray(v) ? (v as Record<string, unknown>) : {};
}

function coerce(kind: string, raw: string): unknown {
  if (kind === "number") {
    const n = Number(raw);
    return Number.isFinite(n) ? n : raw;
  }
  if (kind === "bool") return raw === "true";
  return raw;
}

// Build rows from the served schema + seed overlay. `mode` selects how the
// overlay seeds each row: search-space reads `nodes.{n}.{config, optimizer}`
// (lock/allow/origin-value); values reads the flat `{node:{param:value}}` delta.
// `node` scopes to one node (search-space, per-node); omit for whole-pipeline
// (values).
export function configRows(
  schema: Record<string, NodeConfigParam[]> | null,
  overlay: Record<string, unknown>,
  mode: ConfigMode,
  node?: string,
): ConfigRow[] {
  if (!schema) return [];
  const scoped = node != null ? schema[node] : undefined;
  const entries: [string, NodeConfigParam[]][] =
    node != null ? (scoped ? [[node, scoped]] : []) : Object.entries(schema);
  const rows: ConfigRow[] = [];
  for (const [n, params] of entries) {
    const nodeOv = asObj(overlay[n]);
    const cfg = asObj(nodeOv.config);
    const opt = nodeOv.optimizer as
      | { param_keys?: string[]; param_allowed_values?: Record<string, string[]> }
      | undefined;
    const overlayKeys = opt?.param_keys;
    const overlayAllowed = opt?.param_allowed_values ?? {};
    for (const p of params) {
      const baseValue = p.value == null ? "" : String(p.value);
      const narrowed = overlayAllowed[p.key];
      if (mode === "search-space") {
        const isModel = p.kind === "model";
        // model/provider are always optimizer-locked; every other param inherits
        // the overlay's open-set (or the schema's tunability when unset).
        const locked = isModel
          ? true
          : overlayKeys !== undefined
            ? !overlayKeys.includes(p.key)
            : !p.optimizer_tunable;
        rows.push({
          node: n,
          key: p.key,
          kind: p.kind,
          options: p.options,
          value: p.key in cfg ? String(cfg[p.key]) : baseValue,
          baseValue,
          locked,
          allowed: narrowed ?? p.options,
          fromCandidate: false,
          optimizerLocked: p.optimizer_locked,
          description: p.description,
        });
      } else {
        // values: the seed overlay is the flat fork delta — `nodeOv[key]` is the
        // value directly (no `config`/`optimizer` nesting).
        const fromCandidate = p.key in nodeOv;
        const seedVal = fromCandidate ? nodeOv[p.key] : p.value;
        rows.push({
          node: n,
          key: p.key,
          kind: p.kind,
          options: p.options,
          value: seedVal == null ? "" : String(seedVal),
          baseValue,
          locked: false,
          allowed: p.options,
          fromCandidate,
          optimizerLocked: p.optimizer_locked,
          description: p.description,
        });
      }
    }
  }
  return rows;
}

// search-space emit: merge this node's rows onto the draft overlay → patch.
// `param_keys` = the open (unlocked) non-model params; `param_allowed_values`
// narrows an open enum only when a strict subset; `config` carries changed origin
// values (incl. the chosen model). model/provider are always optimizer-locked, so
// there is no model-lock knob to emit.
export function nodeOverlayPatch(
  base: Record<string, unknown>,
  node: string,
  rows: ConfigRow[],
): DraftPatch {
  const overlay = JSON.parse(JSON.stringify(base)) as Record<string, Record<string, unknown>>;
  const config: Record<string, unknown> = {};
  const paramKeys: string[] = [];
  const allowedValues: Record<string, string[]> = {};

  for (const r of rows) {
    if (r.kind !== "model" && !r.locked) {
      paramKeys.push(r.key);
      if (r.kind === "enum" && r.allowed.length > 0 && r.allowed.length < r.options.length) {
        allowedValues[r.key] = r.allowed;
      }
    }
    if (r.value !== r.baseValue && r.value !== "") {
      config[r.key] = coerce(r.kind, r.value);
    }
  }

  const prev = (overlay[node] ?? {}) as Record<string, unknown>;
  const prevConfig = (prev.config ?? {}) as Record<string, unknown>;
  overlay[node] = {
    ...prev,
    optimizer: { param_keys: paramKeys, param_allowed_values: allowedValues },
    ...(Object.keys(config).length > 0 ? { config: { ...prevConfig, ...config } } : {}),
  };
  return { pipeline_overlay: overlay };
}

// values emit: the sparse `{node:{param:value}}` fork seed, from the rows + the
// operator's edited string values (keyed `"{node}.{key}"`). A param lands when it
// came from the candidate OR the operator changed it from the seeded value —
// inherited-untouched params stay out (they already live in `pipeline_params`).
export function seedOverlayFromRows(
  rows: ConfigRow[],
  edits: Record<string, string>,
): Record<string, Record<string, unknown>> {
  const overlay: Record<string, Record<string, unknown>> = {};
  for (const r of rows) {
    const edit = edits[`${r.node}.${r.key}`];
    if (!r.fromCandidate && (edit === undefined || edit === r.value)) continue; // inherited + untouched
    const raw = edit ?? r.value;
    if (raw === "") continue; // empty = drop (inherit)
    (overlay[r.node] ??= {})[r.key] = coerce(r.kind, raw);
  }
  return overlay;
}
