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
//     `session.pipeline_params` at fork init.
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
  // NEVER a search axis (model/provider — a confound guard). Shown as a hint; a
  // cap-holding operator may still set it on a fork (a babysit edit).
  optimizerLocked: boolean;
  // Who searches this axis right now — served, and the reason a shut axis and an axis
  // nobody happens to be moving are two different rows rather than one badge.
  movableBy: string[];
  // The dataset offered this axis and THIS campaign closed it. The only shut state a
  // person caused, and the only one worth offering a click.
  held: boolean;
  description: string;
}

// The kinds this editor draws a widget for. The served param list is COMPLETE per
// node (that is what makes `movable_by` summable into "where does the search reach
// here"), so it also carries `prompt` (a PromptTemplate decomposition
// field, owned by the prompt editor) and `nested` (object/array — a nested value in
// a text box is a corrupt edit waiting to happen). Those are listed, not rendered:
// the filter belongs here, at the surface that knows what it can draw, not at the
// server, where dropping them blinds every other reader.
export const WIDGET_KINDS: ReadonlySet<string> = new Set([
  "model",
  "enum",
  "number",
  "bool",
  "string",
]);

export function isWidgetParam(p: { kind: string }): boolean {
  return WIDGET_KINDS.has(p.kind);
}

// WHERE THE SEARCH REACHES on one node — the single reading the picture and the config rows
// both take, so a glyph on the graph and the 🔒 beside a param cannot disagree.
//
// A lock is (axis, AGENT), never an axis alone. The server says who may move each param
// (`movable_by`); this counts. The denominator is the DECLARED axes — params that are open, or
// that were open and this campaign closed (`held`) — never the whole param list: `model`,
// `provider` and plain configuration are not axes, and counting them draws every plumbing node
// as locked.
export interface NodeReach {
  /** Axes some agent may move right now. */
  open: number;
  /** Every axis that COULD be opened — the open ones plus the shut ones. */
  openable: number;
  /** Which agents reach this node at all, in `MOVABLE_AGENTS` order. */
  agents: string[];
  /** Some shut axis was shut by a NARROWING, not merely left closed by the dataset. */
  held: boolean;
  // `nothing` is the only state with no lock: every param here is `model`/`provider`, or the
  // node carries none — nothing that could ever be opened, so nothing to draw shut.
  state: "open" | "partial" | "locked" | "nothing";
}

// `null` is UNKNOWN — the schema has not loaded, or this node is absent from it. Distinct from
// `nothing`, and never drawn as a lock: an unread node depicted as shut is a claim nobody made.
//
// The denominator is what is OPENABLE, and that is nearly every param: adding a key to a node's
// `param_keys` is what opens an axis, so a config param no agent moves is shut, not exempt.
// Only `PARAM_FORBIDDEN_KEYS` (`optimizer_locked` — model and provider, held so a comparison
// isn't confounded) is outside the question, and it wears its own badge rather than a padlock.
export function nodeReach(
  schema: Record<string, NodeConfigParam[]> | null,
  node: string,
): NodeReach | null {
  const params = schema?.[node];
  if (params == null) return null;
  const openable = params.filter((p) => !p.optimizer_locked);
  const open = openable.filter((p) => p.movable_by.length > 0);
  const agents = [...new Set(open.flatMap((p) => p.movable_by))];
  const state =
    openable.length === 0
      ? "nothing"
      : open.length === 0
        ? "locked"
        : open.length === openable.length
          ? "open"
          : "partial";
  return {
    open: open.length,
    openable: openable.length,
    agents,
    held: openable.some((p) => p.held),
    state,
  };
}

// What the agent list says out loud. `l1` fires every round and `l2` only on a stall, so they
// are not interchangeable and a bare "the optimizer" would flatten them.
export function agentLabel(agent: string): string {
  return agent === "l1"
    ? "the generator, every round"
    : agent === "l2"
      ? "escalation, when a round stalls"
      : agent;
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
      if (!isWidgetParam(p)) continue;
      const baseValue = p.value == null ? "" : String(p.value);
      const narrowed = overlayAllowed[p.key];
      if (mode === "search-space") {
        const isModel = p.kind === "model";
        // model/provider are always optimizer-locked; every other param inherits
        // the overlay's open-set, or — unset — whether any agent reaches it today.
        const locked = isModel
          ? true
          : overlayKeys !== undefined
            ? !overlayKeys.includes(p.key)
            : p.movable_by.length === 0;
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
          movableBy: p.movable_by,
          held: p.held,
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
          movableBy: p.movable_by,
          held: p.held,
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

/** The flat `node.param` spelling, minted in ONE place. `seedOverlayFromRows` keys its edit map
 *  on it, the server's `flatten_sp_summary` writes it on the wire, and the Compare tab's scenario
 *  edits are keyed on it — so a second function inventing the same string is a drift waiting to
 *  happen. */
export function flatConfigKey(node: string, param: string): string {
  return `${node}.${param}`;
}

// What the operator actually CHANGED — the values editor's emission minus the config it was
// seeded from.
//
// **The emission is not a diff, and reading it as one is the trap.** `configRows` sets
// `fromCandidate` for every param the resolved config carries, and a searchpoint's resolved config
// carries every param's running value rather than a sparse delta. So `seedOverlayFromRows` emits
// the WHOLE running configuration on the first keystroke, and anything treating that as "what
// changed" marks every parameter edited at once.
//
// Compared as STRINGS, and against the browser's own flattening of the seed — never against the
// server-rendered `SubjectReading.config`, whose `_fmt_pp_val` is Python `str`: a bool reads
// `"True"` there and `"true"` from every widget here, which would report a phantom edit on every
// boolean param.
export function overlayEdits(
  emitted: Record<string, Record<string, unknown>>,
  seed: Record<string, unknown>,
): Record<string, string> {
  const out: Record<string, string> = {};
  for (const [node, params] of Object.entries(emitted)) {
    const seeded = asObj(seed[node]);
    for (const [param, value] of Object.entries(params)) {
      const was = param in seeded ? String(seeded[param]) : "";
      if (String(value) !== was) out[flatConfigKey(node, param)] = String(value);
    }
  }
  // A param the seed carried and the emission dropped was CLEARED — `seedOverlayFromRows` drops an
  // empty value to mean "inherit", which is a change like any other and would otherwise vanish.
  for (const [node, params] of Object.entries(seed)) {
    const seeded = asObj(params);
    for (const param of Object.keys(seeded)) {
      if (!(param in (emitted[node] ?? {}))) out[flatConfigKey(node, param)] = "";
    }
  }
  return out;
}

/** The seed with the operator's edits written back in, so the editor re-seeds from their scenario
 *  rather than from the record. That is what makes a restore actually restore: `ValuesEditor`
 *  drops its own draft when the seed changes, so clearing an edit puts the input back by itself. */
export function applyFlatEdits(
  seed: Record<string, unknown>,
  flat: ReadonlyMap<string, string>,
): Record<string, unknown> {
  if (flat.size === 0) return seed;
  const out: Record<string, unknown> = { ...seed };
  for (const [key, value] of flat) {
    const cut = key.indexOf(".");
    if (cut <= 0) continue;
    const node = key.slice(0, cut);
    const param = key.slice(cut + 1);
    out[node] = { ...asObj(out[node]), [param]: value };
  }
  return out;
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
    const edit = edits[flatConfigKey(r.node, r.key)];
    if (!r.fromCandidate && (edit === undefined || edit === r.value)) continue; // inherited + untouched
    const raw = edit ?? r.value;
    if (raw === "") continue; // empty = drop (inherit)
    (overlay[r.node] ??= {})[r.key] = coerce(r.kind, raw);
  }
  return overlay;
}
