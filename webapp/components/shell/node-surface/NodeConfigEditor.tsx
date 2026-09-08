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
  nodeNarrowing,
  nodeOverlayPatch,
  seedOverlayFromRows,
  type ConfigMode,
  type ConfigRow,
} from "@/lib/derivations";

// The one node-config editor, mode-driven:
//   - "search-space" (before mint): the operator declares where this origin STARTS and
//     what the optimizer MAY MOVE. Per-node (`node` scopes it); persists the draft
//     `pipeline_overlay` via `onApply`.
//   - "values" (steer / inspect a fork): the concrete `{node:{param:value}}` the
//     fork is seeded with. Whole-pipeline. Emits the sparse overlay via `onChange`.
//
// **AN AXIS IS A VALUE SET, A PERMITTED SUBSET, AND A START VALUE — nothing else.** Both
// facts the operator controls ride ONE `ui/ValueList`, for `model` and every enum alike: closed
// it is one line, and clicking it drops the list OVER that line. The start value is POSITION 1
// (clicking a value pulls it there, and that IS choosing it) and the ticks are the permitted
// set. Three surfaces used to answer pieces of this separately — a model `<select>`, an
// allow/deny chip strip, and an "Allowed models" panel at the foot of the pipeline block — and
// the operator could not tell they were one question.
//
// Two consequences follow, and neither is separately expressible any more:
//   - **The lock is DERIVED.** One permitted value IS the pin, so an enumerable axis carries
//     no padlock. 🔒 / 🔓 survives only where there is no set to narrow — number, string,
//     bool — plus the node-level master lock over exactly those.
//   - **`model` is an axis like any other.** It left `PARAM_FORBIDDEN_KEYS`; its ticks are
//     the ONE permitted model set, which is both what the optimizer may pick and what a
//     human fork may steer to un-tainted.
//
// The PICKED MODEL qualifies the reasoning ladder — REPLACING what the node declared, since a
// node's list is a default authored before anyone knew which model would run there. Its
// metadata card renders under the model row, so a stale or misconfigured pick is visible.
//
// **A CHANNEL THE HOST DOES NOT PASS IS A CONTROL THAT DOES NOT DRAW** (`NodeSurface`'s rule,
// applied per fact rather than per editor). Search-space carries two independent ones: `onApply`
// owns where the origin STARTS, `onNarrowing` owns what the optimizer MAY DO. The steer fork
// passes only the second — its values are steered above — and so gets ticks and padlocks over
// plain text, with nothing on screen inviting a click that would be discarded. `readOnly` is the
// separate case of a host that owns a channel and has it withheld.
export function NodeConfigEditor(props: {
  mode: ConfigMode;
  schema: Record<string, NodeConfigParam[]> | null;
  seedOverlay: Record<string, unknown>;
  node?: string;
  readOnly?: boolean;
  // values mode only: when false, a model the origin does not permit is held read-only —
  // steering to one is the ADR-0005 babysit act, allowed only for a principal holding
  // `campaign.babysit`. Default true keeps every other caller (draft setup, inspect)
  // unchanged; the steer form passes the operator's cap.
  babysitEditable?: boolean;
  // values mode only: fold params still sitting at the pipeline default behind a
  // disclosure, leaving the ones this searchpoint actually moved. For the half-width
  // hosts (the chat run card) where the full table does not fit. Authoring
  // (search-space) never compacts — a hidden lock is a lock nobody set.
  compact?: boolean;
  // What each model on the menu ACCEPTS and costs, keyed by model id. Qualifies the reasoning
  // row and backs the metadata card. Absent = nothing resolved, which every reader renders as
  // UNKNOWN — never as a menu of unsupported models.
  modelCapabilities?: Record<string, ModelCapability>;
  // values mode only: the origin's per-node permitted model sets, so a steer the operator may
  // not make without the babysit cap is disabled rather than rejected on confirm.
  permittedModels?: Record<string, readonly string[]>;
  onApply?: (patch: DraftPatch) => void;
  // search-space only: this node's permission half alone, for a host whose values are set
  // elsewhere. Emitted alongside `onApply` where both are passed, so the draft origin cannot
  // drift from the narrowing it implies.
  onNarrowing?: (narrowing: NodeSearchNarrowing) => void;
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

/** The menu one axis offers, plus the two provenance facts about it — kept apart, because they
 *  answer different questions and a value can carry both.
 *
 *  `ladder` is the model's answer where it has one (`reasoning_effort`), the node's declared
 *  options otherwise. `values` unions in what is PERMITTED, so a value the operator ticked and
 *  something then refused stays on screen rather than vanishing with its permission still on the
 *  wire. The start value leads by prepend-and-filter, not by a comparator: "move one element to
 *  the front" is not a valid total order and sorts only accidentally stably.
 *
 *  `inert` = the picked MODEL refuses it, and only where the model actually answered — an UNKNOWN
 *  capability must never strike a rung, which is the rule the whole capability layer exists for.
 *  `userAdded` = neither the node nor the model offered it, so the operator typed it. That is why
 *  `available_models` is served as the ADMIN's catalogue alone: folding an operator's additions
 *  into it would erase the only difference this reads. */
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

// search-space: a value list per enumerable axis, a padlock per free-valued one, the node
// master lock. Local rows are the source of truth during an edit; reseed (the render-phase
// guarded reset) only when the viewed node changes.
function SearchSpaceEditor({
  schema,
  seedOverlay,
  node,
  readOnly = false,
  modelCapabilities,
  onApply,
  onNarrowing,
}: {
  schema: Record<string, NodeConfigParam[]> | null;
  seedOverlay: Record<string, unknown>;
  node?: string;
  readOnly?: boolean;
  modelCapabilities?: Record<string, ModelCapability>;
  onApply?: (patch: DraftPatch) => void;
  onNarrowing?: (narrowing: NodeSearchNarrowing) => void;
}) {
  const nodeId = node ?? "";
  const [prevNode, setPrevNode] = useState(nodeId);
  const [rows, setRows] = useState<ConfigRow[]>(() =>
    configRows(schema, seedOverlay, "search-space", nodeId),
  );
  if (nodeId !== prevNode) {
    setPrevNode(nodeId);
    setRows(configRows(schema, seedOverlay, "search-space", nodeId));
  }

  if (rows.length === 0) return <EmptyConfig />;

  // A single-node pipeline (one node in the served config schema) is UNLOCKABLE:
  // locking its lone node would leave the optimizer with nothing to tune. Mirrors
  // PipelineSchema.is_single_node. The lock is an OPTIMIZER-search-space concept,
  // independent of this connector node — so for a single node the lock affordance
  // is suppressed entirely (master + per-param); the operator still sets origin
  // values and narrows the permitted sets.
  const singleNode = schema != null && Object.keys(schema).length <= 1;

  // Both channels fire, each only where the host owns it. Sending the narrowing to a host that
  // asked for a whole patch — or a patch to one that asked for the narrowing — is the unwrapping
  // this pair exists to delete.
  const persist = (next: ConfigRow[]) => {
    onApply?.(nodeOverlayPatch(seedOverlay, nodeId, next));
    onNarrowing?.(nodeNarrowing(next));
  };
  const update = (i: number, patch: Partial<ConfigRow>) => {
    const next = rows.map((r, j) => (j === i ? { ...r, ...patch } : r));
    setRows(next);
    persist(next);
  };

  // The node-level master lock governs the FREE-VALUED params — the only ones `locked` still
  // decides. An enumerable axis pins by being ticked down to one value, a different gesture that
  // a master switch cannot express, so sweeping those too would set a state nobody chose.
  const freeValued = rows.filter((r) => r.kind !== "model" && r.kind !== "enum");
  const nodeLocked = freeValued.length > 0 && freeValued.every((r) => r.locked);
  const toggleNodeLock = () => {
    const v = !nodeLocked;
    const next = rows.map((r) =>
      r.kind === "model" || r.kind === "enum" ? r : { ...r, locked: v },
    );
    setRows(next);
    persist(next);
  };

  const toggle = (i: number, value: string) => {
    const r = rows[i];
    if (!r) return;
    const on = r.allowed.includes(value);
    const next = on ? r.allowed.filter((v) => v !== value) : [...r.allowed, value];
    if (next.length === 0) return; // an axis with nothing permitted has nothing to run
    update(i, { allowed: next });
  };
  // A value the catalogue does not carry — a model added since the menu was written, a rung the
  // node never declared. It joins the PERMITTED set, which is where a widening rides: the overlay
  // states the axis's whole value space and `PipelineSchema.narrow` REPLACES rather than
  // intersects (only `param_keys` subsets), so it survives the mint on a reused dataset too.
  const add = (i: number, value: string) => {
    const r = rows[i];
    if (!r || r.allowed.includes(value)) return;
    update(i, { allowed: [...r.allowed, value] });
  };

  // The picked model qualifies the reasoning row and backs the card. Measured:
  // `qwen/qwen3.7-flash` takes `reasoning`/`include_reasoning` and no `reasoning_effort` at all,
  // so a node's ladder is inert on it — the axis would otherwise read as live and the optimizer
  // would spend rounds moving a parameter nobody receives.
  const pickedModel = rows.find((r) => r.kind === "model")?.value ?? "";
  const caps = modelCapabilities?.[pickedModel];

  return (
    <div className="config-editor">
      {rows.map((r, i) => {
        const enumerable = r.kind === "model" || r.kind === "enum";
        if (!enumerable) {
          return (
            <ConfigRowView
              key={r.key}
              row={r}
              readOnly={readOnly}
              // The general case of the struck rungs below: a key the picked model does not
              // accept. `unsupported_params` is the SERVED answer over what we actually send, so
              // `undefined` here means the catalogue said nothing and the row claims nothing.
              ignoredBy={caps?.unsupported_params?.includes(r.key) ? pickedModel : undefined}
              onToggleLock={singleNode ? undefined : () => update(i, { locked: !r.locked })}
              onValue={onApply ? (v) => update(i, { value: v }) : undefined}
            />
          );
        }
        const { values, inert, userAdded } = axisMenu(r, caps);
        return (
          <div key={r.key} className="config-row">
            <span className="config-label" title={r.description || undefined}>
              {r.key}
            </span>
            <span className="config-value">
              <ValueList
                name={r.key}
                values={values}
                checked={r.allowed}
                inert={inert}
                userAdded={userAdded}
                note={axisNote(r, caps, pickedModel)}
                readOnly={readOnly}
                addPlaceholder={r.kind === "model" ? "another model id…" : "another value…"}
                onPick={onApply ? (v) => update(i, { value: v }) : undefined}
                onToggle={(v) => toggle(i, v)}
                onAdd={(v) => add(i, v)}
              />
              {r.kind === "model" && caps ? <ModelCard caps={caps} /> : null}
            </span>
          </div>
        );
      })}

      {singleNode ? (
        <small className="config-hint">
Click an axis to drop its list. The first value is where this origin starts —
          click another to move it there; ☑ = permitted, and one value left pins the axis.
          Single-node pipeline, so there is no whole-node lock: holding the only node would
          leave nothing to tune.
        </small>
      ) : (
        <>
          {/* The whole-node tuning control sits BELOW the params it governs, not as a
              header over them — locking is an optimizer-search-space lever on this
              node, not a label on the connector pipeline. */}
          {freeValued.length > 0 ? (
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
          <small className="config-hint">
Click an axis to drop its list. The first value is where this origin starts —
            click another to move it there; ☑ = permitted, and one value left pins the axis.
            🔒 / 🔓 = held / tunable, for the params that carry no value list.
          </small>
        </>
      )}
    </div>
  );
}

/** One served line under a value list: who the ticks license, and — on the reasoning row —
 *  whose claim moved it. Every arm of `reasoning_note` is populated server-side, so an unknown
 *  model SAYS it is unknown rather than rendering as a silent full ladder. */
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

/** What the provider says about the picked model. Only what is PRESENT renders — the catalogue
 *  is a third party's claim, and a field it drops must degrade the card rather than blank it. */
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
  babysitEditable = true,
  compact = false,
  permittedModels,
  onChange,
}: {
  schema: Record<string, NodeConfigParam[]> | null;
  seedOverlay: Record<string, unknown>;
  node?: string;
  readOnly?: boolean;
  babysitEditable?: boolean;
  compact?: boolean;
  permittedModels?: Record<string, readonly string[]>;
  onChange?: (overlay: Record<string, Record<string, unknown>>) => void;
}) {
  const rows = useMemo(
    () => configRows(schema, seedOverlay, "values", node),
    [schema, seedOverlay, node],
  );
  const [edits, setEdits] = useState<Record<string, string>>({});
  // Render-phase guarded reset (webapp/CLAUDE.md § State reset on prop change):
  // when the seed changes the prior edits no longer apply. The seed loads ASYNC
  // in SteerForkPanel (useRoundFile) — `{}` first, then the candidate's resolved
  // config — so an edit made before it lands must not keep masking the freshly
  // seeded `r.value` below.
  const sig = `${node ?? ""}|${JSON.stringify(seedOverlay)}`;
  const [prevSig, setPrevSig] = useState(sig);
  if (sig !== prevSig) {
    setPrevSig(sig);
    setEdits({});
  }

  if (rows.length === 0) return <EmptyConfig />;

  const set = (key: string, v: string) => {
    const next = { ...edits, [key]: v };
    setEdits(next);
    onChange?.(seedOverlayFromRows(rows, next));
  };

  const renderRow = (r: ConfigRow) => {
    const key = `${r.node}.${r.key}`;
    const edit = edits[key];
    const value = edit !== undefined ? edit : r.value;
    // Steering the model OUTSIDE what the origin permits is the babysit act, and it taints the
    // branch (grade C) on the backend. Without the cap the row still shows every model the menu
    // carries — the un-permitted ones simply cannot be picked, which is a truer read than a row
    // that looks editable and 404s on confirm.
    const unpermitted =
      r.kind === "model" && !babysitEditable ? (permittedModels?.[r.node] ?? []) : undefined;
    return (
      <ConfigRowView
        key={key}
        row={{ ...r, value }}
        values
        readOnly={readOnly || (!babysitEditable && r.optimizerLocked)}
        permitted={unpermitted}
        onValue={(v) => set(key, v)}
      />
    );
  };

  if (!compact) return <div className="config-editor">{rows.map(renderRow)}</div>;

  // Compact: lead with the params this searchpoint MOVED. `value !== baseValue` is
  // the same predicate `nodeOverlayPatch` writes an override on, so the two cannot
  // disagree about what counts as moved — and it is not `fromCandidate`, which the
  // OBSERVE path leaves true for nearly every row (the resolved config carries every
  // param's running value, not a sparse delta). Nothing is hidden, only folded.
  const moved = rows.filter((r) => r.value !== r.baseValue);
  const atDefault = rows.filter((r) => r.value === r.baseValue);
  return (
    <div className="config-editor is-compact">
      {moved.length > 0 ? (
        moved.map(renderRow)
      ) : (
        <small className="config-hint">Every param is still at its pipeline default.</small>
      )}
      {atDefault.length > 0 ? (
        <details className="config-rest">
          <summary>{atDefault.length} more at default</summary>
          {atDefault.map(renderRow)}
        </details>
      ) : null}
    </div>
  );
}

// One row of concrete config. Serves the values editor throughout, and the search-space editor
// for its FREE-VALUED params alone — an enumerable axis there is a `ValueList` and takes none of
// this chrome. `values` says which of the two is asking: it decides the badge (who searches this)
// and whether a padlock is on offer at all.
function ConfigRowView({
  row,
  values = false,
  readOnly,
  permitted,
  ignoredBy,
  onToggleLock,
  onValue,
}: {
  row: ConfigRow;
  values?: boolean;
  readOnly: boolean;
  // The picked model, when it does NOT accept this key — so the row says the value is dropped
  // rather than showing it as a live setting. Undefined = accepted, or the catalogue never said.
  ignoredBy?: string;
  // values mode: the models this node permits. Options outside it are disabled — steering
  // there is the babysit act and this principal lacks the cap. Undefined = no restriction.
  permitted?: readonly string[];
  // Absent = this host does not set the padlock (a single-node pipeline, whose lone node cannot
  // be held) or the value (a permissions-only host). Each renders as what it is instead.
  onToggleLock?: () => void;
  onValue?: (v: string) => void;
}) {
  const isModel = row.kind === "model";
  return (
    <div className="config-row">
      <span className="config-label">
        {row.key}
        {values && row.fromCandidate ? (
          <span className="config-evolved" title="Carried from this searchpoint">
            ·evolved
          </span>
        ) : null}
        {/* A setting the provider DROPS is the one thing a config row must not render as live:
            the value sits there looking set, the model never receives it, and nothing anywhere
            says so — which is how `reasoning_effort: low` read as a bound on a model that emitted
            99% reasoning tokens. Wears the same badge as a held axis because it is the same fact
            to a reader: not in play, reason in the title. */}
        {ignoredBy ? (
          <span
            className="config-optlocked"
            title={`${ignoredBy} does not accept ${row.key} — it is dropped by the provider, so this value has no effect.`}
          >
            ⊘
          </span>
        ) : null}
        {/* Two states on screen — the optimizer may move this axis, or it may not — because
            that is the only question a row is read for. The three reasons it may not are three
            different operator remedies, so they ride the title, read one row at a time. In
            search-space the LockButton beside the row already answers it, so the badge would
            only repeat it. */}
        {values ? (
          row.movableBy.length > 0 ? (
            <span
              className="config-optmovable"
              title={`Searched by ${row.movableBy.map(agentLabel).join(", ")}.`}
            >
              {row.movableBy.join("+")}
            </span>
          ) : (
            <span className="config-optlocked" title={lockReason(row, readOnly)}>
              🔒
            </span>
          )
        ) : null}
      </span>
      <span className="config-value">
        {onToggleLock ? (
          <LockButton locked={row.locked} readOnly={readOnly} onClick={onToggleLock} />
        ) : null}
        {!onValue ? (
          // No value channel — a sibling surface sets this one. Text rather than a disabled
          // input: a greyed box says "you may not", where the truth is "not here".
          <span className="config-static">{row.value || "—"}</span>
        ) : isModel || row.kind === "enum" ? (
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
              <option key={m} value={m} disabled={permitted !== undefined && !permitted.includes(m)}>
                {m}
                {permitted !== undefined && !permitted.includes(m) ? " (needs babysit)" : ""}
              </option>
            ))}
          </select>
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
          // Commits on Enter or blur, never per keystroke. It only mutated a ref inside a modal
          // once, so nobody saw it — but the same emission now strikes a searchpoint and
          // everything descending from it off the Compare cladogram, and per-keystroke that
          // happens on `"1"` en route to `"12"`.
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
      </span>
    </div>
  );
}

// `optimizerLocked` (PARAM_FORBIDDEN_KEYS — `provider` and `route_order`) outranks `held` (the
// campaign's own narrowing at mint): a cost lever stays a cost lever however the campaign
// narrowed. `model` reaches neither arm any more — it is an ordinary axis, so it reads as held
// or as unsearched like every other.
function lockReason(row: ConfigRow, readOnly: boolean): string {
  if (row.optimizerLocked) {
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
