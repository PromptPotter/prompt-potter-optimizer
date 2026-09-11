// The one node-config model + seed + the two overlay-emit paths. A single row
// type (`ConfigRow`) and one seed function (`configRows`) back the mode-driven
// `NodeConfigEditor`; the two emit functions stay distinct because they ride two
// different backend transports:
//
//   - search-space → `nodeOverlayPatch` → the draft `pipeline_overlay`
//     (`nodes.{n}.{config, optimizer}`); at mint the backend splits it into the
//     per-campaign `pipeline_overlay` (config values) + `optimizer_narrowing`
//     (the param-lock / allowed-values subset). See `launcher.split_overlay`.
//   - values → `seedOverlayFromRows` → the flat `{node:{param:value}}` overlay
//     that `OperatorForkOverride.pipeline_overlay` merges onto
//     `session.pipeline_params` at fork init.
//
// Pure data→data (no React, no I/O) so it rides the lib Vitest scope.

import type { DraftPatch, ModelCapability, NodeConfigParam } from "@/lib/api";
import type { NodeSearchNarrowing } from "@/lib/api/types";

export type ConfigMode = "search-space" | "values";

// One row of the config editor. Search-space fields (`locked`, `allowed`,
// `baseValue`) drive the optimizer search-space lever; values fields
// (`fromCandidate`, `neverAxis`) drive the concrete fork-value lever. A row
// carries both; the active `mode` decides which it renders + emits.
export interface ConfigRow {
  node: string;
  key: string;
  // "model" | "enum" | "number" | "bool" | "string" | "nested" — drives the widget + coercion
  // (enums carry a narrowed allow-set; `nested` round-trips through JSON).
  kind: string;
  // Full declared value set (model/enum); empty otherwise.
  options: string[];
  // Current (editable) value, stringified for the input. "" when neither the
  // overlay nor the schema carries it (declared but unset, e.g. max_tokens).
  value: string;
  // The schema's value (origin). A change vs this lands the param in `config`
  // (search-space).
  baseValue: string;
  // search-space: the optimizer may NOT move this param (it leaves `param_keys`).
  //
  // **Only a FREE-VALUED param carries this.** An enumerable axis — `model` and every enum —
  // locks by being ticked down to one permitted value, so `allowed.length <= 1` IS its lock and
  // a second boolean beside it could only contradict it. `number`/`string`/`bool` have no set to
  // narrow, so there the padlock is the control.
  locked: boolean;
  // search-space: the permitted subset. What the optimizer may pick where the axis is open, and
  // (for `model`) what a human may steer a fork to without grading the branch C — ONE set,
  // because they were one question asked twice.
  allowed: string[];
  // search-space: the server STATED `permitted` — an absent entry would not resolve to it — so the
  // emit writes the set out even where it equals the menu.
  stated: boolean;
  // values: present in the candidate overlay (vs the config floor) — keep it in
  // the emitted overlay even when the operator leaves it untouched.
  fromCandidate: boolean;
  // WHICH construction forbids this key from ever being an axis, "" where none does — served,
  // because a browser reading it off the key's name can only ever tell one of the two stories.
  // Shown as a hint; a cap-holding operator may still set a cost lever on a fork (a babysit
  // edit), while a schema-owned key is the contract itself and moves nowhere.
  neverAxis: NodeConfigParam["never_axis"];
  // Who searches this axis right now — served, and the reason a shut axis and an axis
  // nobody happens to be moving are two different rows rather than one badge.
  movableBy: string[];
  // The dataset offered this axis and THIS campaign closed it. The only shut state a
  // person caused, and the only one worth offering a click.
  held: boolean;
  description: string;
}

/** The ladder a `reasoning_effort` row offers on the MENU, once a model is picked.
 *
 *  **Display only — this is not the engine's resolve, and must not become it.** `param_options`
 *  intersects a campaign's narrowing; this unions the model's answer into the menu instead, and
 *  the row's TICKS (`allowed` ← served `permitted`) stay the operator's own declaration, because
 *  those are what `nodeNarrowing` emits back. A rung ticked here and refused by the model renders
 *  ticked AND struck (`axisMenu::inert`) — two facts the operator can act on separately, where
 *  pre-multiplying them would let a repaint save the model's refusals as the campaign's choice.
 *
 *  Unknown — the model is absent from the catalogue snapshot, or none was fetched — falls back to
 *  the node's list untouched, because an absent answer rendered as "no" deletes a search axis. */
export function effortLadder(row: ConfigRow, caps: ModelCapability | undefined): string[] {
  if (row.key !== "reasoning_effort") return row.options;
  return caps?.reasoning_efforts ?? row.options;
}

/** Client twin of the Python `overlay_sets_model_outside_allowed`
 *  (`promptpotter/domain/pipeline_overlay.py`). True iff a fork's `pipeline_overlay` steers a node
 *  to a responder the origin has NOT permitted — the ADR-0005 babysit (grade-C) trigger. Keeps the
 *  client warning on the SAME predicate the server gate enforces at `fork-cycle`.
 *
 *  *permitted* is per NODE — the frozen `config.optimizer_narrowing[node].param_allowed_values
 *  .model`, which is the ONE permitted set. A node absent from it permits nothing, the restrictive
 *  default. A cost lever — the gateway or the route, the two keys served as `never_axis:
 *  "cost_lever"` — has no permitted set that could sanction it, so an edit to one always counts. */
export function overlaySetsModelOutsideAllowed(
  overlay: Record<string, unknown> | null | undefined,
  permitted: Record<string, readonly string[]> | null | undefined,
): boolean {
  for (const [node, cfg] of Object.entries(overlay ?? {})) {
    if (!cfg || typeof cfg !== "object" || Array.isArray(cfg)) continue;
    const c = cfg as Record<string, unknown>;
    if ("provider" in c || "route_order" in c) return true;
    const model = c.model;
    if (model != null && !new Set(permitted?.[node] ?? []).has(String(model))) return true;
  }
  return false;
}

/** The per-node permitted model sets, read off the SERVED rows — `permitted` when the gate accepts
 *  something narrower than the menu, and `options` when it does not. That `null` is not `[]` is the
 *  whole distinction: `[]` says nothing may be picked, while `null` says `options` IS the permitted
 *  set (`domain/pipeline_schema.py::NodeConfigParam.permitted`).
 *
 *  Off THESE rows and never off the campaign's frozen `config.optimizer_narrowing`: that one
 *  answers for the mint, so a fork or a cycle seed that moved the set steers against the wrong
 *  list. */
export function permittedModels(
  schema: Record<string, NodeConfigParam[]> | null | undefined,
): Record<string, readonly string[]> {
  const out: Record<string, readonly string[]> = {};
  for (const [node, params] of Object.entries(schema ?? {})) {
    const row = params.find((p) => p.key === "model");
    if (row) out[node] = row.permitted ?? row.options;
  }
  return out;
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

// A row's value as TEXT. `String()` on an object is `[object Object]`, which is how an axis with a
// real value on the wire would arrive on screen saying nothing at all.
function asRowValue(kind: string, value: unknown): string {
  if (value == null) return "";
  return kind === "nested" ? JSON.stringify(value, null, 1) : String(value);
}

// `undefined` means UNREPRESENTABLE — a draft that cannot become the value this row holds. Only
// `nested` can produce it, and the emitters drop such a row rather than writing a string where an
// object belongs. Distinct from `""`, which is a real answer meaning "inherit".
function coerce(kind: string, raw: string): unknown {
  if (kind === "number") {
    const n = Number(raw);
    return Number.isFinite(n) ? n : raw;
  }
  if (kind === "bool") return raw === "true";
  if (kind === "nested") return parseNested(raw);
  return raw;
}

/** A nested row's draft as the object it stands for, `""` for an empty one (which both emitters
 *  drop, meaning inherit), or `undefined` if it is not parseable yet. Exported because the WIDGET
 *  asks the same question to decide whether to accept a commit — two spellings of "is this valid
 *  JSON" is how a box comes to accept what the emitter drops. */
export function parseNested(raw: string): unknown {
  if (raw.trim() === "") return "";
  try {
    return JSON.parse(raw);
  } catch {
    return undefined;
  }
}

/** The schema a check-in AUTHORED on this node — the draft's own overlay, never the served
 *  resolution, which answers nothing while the node is set to `text`. */
export function authoredOutputSchema(
  overlay: Record<string, unknown>,
  node: string,
): Record<string, unknown> | undefined {
  const schema = asObj(asObj(asObj(overlay[node]).config).output_schema);
  return Object.keys(schema).length > 0 ? schema : undefined;
}

/** The slot that authored contract names as the answer, off the same overlay. */
export function authoredAnswerField(
  overlay: Record<string, unknown>,
  node: string,
): string | undefined {
  const field = asObj(asObj(overlay[node]).config).answer_field;
  return typeof field === "string" ? field : undefined;
}

/** The server's `description_key` spelling (`domain/pipeline_schema.py::SCHEMA_DESCRIPTION_PREFIX`):
 *  one served `description` row per output-schema field, keyed by the field's dotted path. */
export const DESCRIPTION_PREFIX = "output_schema_descriptions.";

/** The description keys one click on a schema-tree row flips: the field's and every one beneath it,
 *  `""` being the whole schema. That gesture IS the lock's inheritance — a descendant unlocked
 *  afterwards holds for its own subtree, because nothing else ever writes these keys. */
export function descriptionSubtree(keys: readonly string[], path: string): string[] {
  const key = DESCRIPTION_PREFIX + path;
  return path === "" ? [...keys] : keys.filter((k) => k === key || k.startsWith(`${key}.`));
}

/** search-space emit for an AUTHORED output contract, in one patch: the schema and the slot that IS
 *  the answer — `answer` (the tree's chip) where given, else the one already chosen while it still
 *  exists, else a field called `answer`, else the LAST: fields generate in order, so the reasoning
 *  comes first (`docs/concepts/structured-output.md`).
 *
 *  A FIRST schema also sets `response_format: json` — one nothing answers under is a declaration
 *  nothing reads — and re-opens the toggle: a narrowing saved before a schema existed shut it
 *  (`param_keys` is a set that replaces the node's own, and it could only be ticked `text`). The
 *  description keys are the server's to keep in step with the fields (`draft_patch.py`). A re-edit
 *  leaves the toggle and the narrowing alone: by then they are the operator's. */
export function nodeSchemaPatch(
  base: Record<string, unknown>,
  node: string,
  schema: Record<string, unknown>,
  answer?: string,
): DraftPatch {
  const overlay = JSON.parse(JSON.stringify(base)) as Record<string, Record<string, unknown>>;
  const prev = asObj(overlay[node]);
  const fields = Object.keys(asObj(schema.properties));
  const first = authoredOutputSchema(base, node) === undefined;
  const chosen = [answer, authoredAnswerField(base, node)].find(
    (f) => f !== undefined && fields.includes(f),
  );
  const optimizer = { ...asObj(prev.optimizer) };
  if (prev.optimizer !== undefined && first) {
    const allowed = { ...asObj(optimizer.param_allowed_values) };
    delete allowed.response_format;
    optimizer.param_allowed_values = allowed;
    if (Array.isArray(optimizer.param_keys)) {
      optimizer.param_keys = [...new Set([...optimizer.param_keys, "response_format"])];
    }
  }
  overlay[node] = {
    ...prev,
    ...(prev.optimizer === undefined ? {} : { optimizer }),
    config: {
      ...asObj(prev.config),
      output_schema: schema,
      answer_field: chosen ?? (fields.includes("answer") ? "answer" : fields[fields.length - 1]),
      ...(first ? { response_format: "json" } : {}),
    },
  };
  return { pipeline_overlay: overlay };
}

// Build rows from the served schema. `node` scopes to one node (search-space, per-node); omit for
// whole-pipeline (values).
//
// **`valuesSeed` is read in `values` mode ONLY, and that asymmetry is the point.** A search-space
// row's value, lock and permitted set are all SERVED — deriving them from a client-held overlay
// re-answers in the browser what the server's merge already settled, and answers differently
// (`frontend-surface-contract.md::I9`). A fork's seed is a sparse delta that exists nowhere else
// until it is confirmed, so `values` takes one: `fromCandidate` is what keeps an
// inherited-but-untouched param out of the emission.
export function configRows(
  schema: Record<string, NodeConfigParam[]> | null,
  valuesSeed: Record<string, unknown>,
  mode: ConfigMode,
  node?: string,
): ConfigRow[] {
  if (!schema) return [];
  const scoped = node != null ? schema[node] : undefined;
  const entries: [string, NodeConfigParam[]][] =
    node != null ? (scoped ? [[node, scoped]] : []) : Object.entries(schema);
  const rows: ConfigRow[] = [];
  for (const [n, params] of entries) {
    const nodeSeed = asObj(valuesSeed[n]);
    for (const p of params) {
      // The served list is COMPLETE per node — that is what makes `movable_by` summable into
      // "where does the search reach here" — and nothing is subtracted: a `prompt` field's lock is
      // a `param_keys` membership like any param's, so every emit must list it. The prompt editor,
      // not the grid, DRAWS it.
      const baseValue = asRowValue(p.kind, p.value);
      // `permitted` is `null` when it does not differ from the menu — NOT `[]`, which says
      // nothing may be picked at all. `??` is what keeps those two apart.
      const permitted = p.permitted ?? p.options;
      if (mode === "search-space") {
        rows.push({
          node: n,
          key: p.key,
          kind: p.kind,
          options: p.options,
          value: baseValue,
          baseValue,
          locked: p.movable_by.length === 0,
          allowed: permitted,
          stated: p.permitted !== null,
          fromCandidate: false,
          neverAxis: p.never_axis,
          movableBy: p.movable_by,
          held: p.held,
          description: p.description,
        });
      } else {
        // values: the seed is the flat fork delta — `nodeSeed[key]` is the value
        // directly (no `config`/`optimizer` nesting).
        const fromCandidate = p.key in nodeSeed;
        const seedVal = fromCandidate ? nodeSeed[p.key] : p.value;
        rows.push({
          node: n,
          key: p.key,
          kind: p.kind,
          options: p.options,
          value: asRowValue(p.kind, seedVal),
          baseValue,
          locked: false,
          // The whole MENU, not the permitted subset: a babysit-capable operator may steer a fork
          // outside it deliberately, taking the grade-C taint. `SteerForkPanel` warns off
          // `permittedModels` instead — restricting here would delete the act.
          allowed: p.options,
          stated: false,
          fromCandidate,
          neverAxis: p.never_axis,
          movableBy: p.movable_by,
          held: p.held,
          description: p.description,
        });
      }
    }
  }
  return rows;
}

/** Whether two value sets hold the same members, order and duplicates ignored. The predicate
 *  `nodeOverlayPatch` writes a `param_allowed_values` entry on: DIFFERS, not "is a strict subset",
 *  because an operator may WIDEN an axis — adding a model the catalogue predates, or a reasoning
 *  rung the node never listed — and a subset test drops exactly that edit on the floor.
 *  `PipelineSchema.narrow` REPLACES `param_allowed_values` (only `param_keys` subsets), so the
 *  widened list survives the mint. */
function sameMembers(a: readonly string[], b: readonly string[]): boolean {
  if (a.length !== b.length) return false;
  const set = new Set(b);
  return a.every((v) => set.has(v));
}

/** WHAT THE OPTIMIZER MAY DO on one node, from its rows — `param_keys` (the open axes) and
 *  `param_allowed_values` (each enumerable axis's permitted set, written whenever it differs from
 *  the menu or the server `stated` it: an absent entry resolves to the DECLARATION, which a
 *  widening or the schema toggle's whole space is not).
 *
 *  Its own function because the two hosts that emit it want DIFFERENT things around it: the draft
 *  origin wraps it in a `DraftPatch` alongside the config values, while the steer fork sends it
 *  alone, as `OperatorForkOverride.optimizer_narrowing`. Read one out of the other's envelope and
 *  the second host is unwrapping a shape the first happened to choose. */
export function nodeNarrowing(rows: ConfigRow[]): NodeSearchNarrowing {
  const paramKeys: string[] = [];
  const allowedValues: Record<string, string[]> = {};
  for (const r of rows) {
    // An enumerable axis narrowed to ONE permitted value is pinned by construction — which is
    // why `locked` on such a row is derived here rather than toggled.
    const enumerable = r.kind === "enum" || r.kind === "model";
    const locked = enumerable ? r.allowed.length <= 1 : r.locked;
    if (!locked) paramKeys.push(r.key);
    if (enumerable && r.allowed.length > 0 && (r.stated || !sameMembers(r.allowed, r.options))) {
      // Written even when the axis is PINNED: the permitted set is what a human fork may steer
      // to un-tainted, which outlives whether the optimizer may move it.
      allowedValues[r.key] = r.allowed;
    }
  }
  return { param_keys: paramKeys, param_allowed_values: allowedValues };
}

// search-space emit: merge this node's rows onto the draft overlay → patch. `config` carries the
// changed origin values (the chosen model included); the permission half is `nodeNarrowing`.
export function nodeOverlayPatch(
  base: Record<string, unknown>,
  node: string,
  rows: ConfigRow[],
): DraftPatch {
  const overlay = JSON.parse(JSON.stringify(base)) as Record<string, Record<string, unknown>>;
  const config: Record<string, unknown> = {};
  for (const r of rows) {
    if (r.value !== r.baseValue && r.value !== "") {
      const value = coerce(r.kind, r.value);
      // Unparseable nested draft: drop it rather than write a string over an object. The
      // permission half is unaffected either way — `nodeNarrowing` builds `param_keys` from
      // UNLOCKED rows, so a schema-owned key can reach `config` (the operator declaring it)
      // and still never become an axis.
      if (value !== undefined) config[r.key] = value;
    }
  }

  const prev = (overlay[node] ?? {}) as Record<string, unknown>;
  const prevConfig = (prev.config ?? {}) as Record<string, unknown>;
  overlay[node] = {
    ...prev,
    optimizer: nodeNarrowing(rows),
    ...(Object.keys(config).length > 0 ? { config: { ...prevConfig, ...config } } : {}),
  };
  return { pipeline_overlay: overlay };
}

/** The ONE lock emitter for a surface outside the grid — a prompt field, an output-schema path: the
 *  served rows with `keys` set to `locked`, through the grid's own emitter, so a lock clicked
 *  there cannot disagree with the rest of the narrowing. */
export function nodeLockPatch(
  schema: Record<string, NodeConfigParam[]> | null,
  overlay: Record<string, unknown>,
  node: string,
  keys: readonly string[],
  locked: boolean,
): DraftPatch {
  return nodeOverlayPatch(
    overlay,
    node,
    configRows(schema, overlay, "search-space", node).map((r) =>
      keys.includes(r.key) ? { ...r, locked } : r,
    ),
  );
}

/** The flat `node.param` spelling, minted in ONE place. `seedOverlayFromRows` keys its edit map
 *  on it, the server's `flatten_sp_summary` writes it on the wire, and the Compare tab's scenario
 *  edits are keyed on it — so a second function inventing the same string is a drift waiting to
 *  happen. */
export function flatConfigKey(node: string, param: string): string {
  return `${node}.${param}`;
}

// What the operator actually CHANGED — a `values`-mode emission minus the config it was seeded
// from.
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
 *  rather than from the record. That is what makes a restore actually restore: `NodeConfigEditor`
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
    if (r.kind === "prompt") continue; // the fork's prompt rides `origin_prompt_fields`, never config
    const edit = edits[flatConfigKey(r.node, r.key)];
    if (!r.fromCandidate && (edit === undefined || edit === r.value)) continue; // inherited + untouched
    const raw = edit ?? r.value;
    if (raw === "") continue; // empty = drop (inherit)
    const value = coerce(r.kind, raw);
    if (value === undefined) continue; // unparseable nested draft — never a string over an object
    (overlay[r.node] ??= {})[r.key] = value;
  }
  return overlay;
}
