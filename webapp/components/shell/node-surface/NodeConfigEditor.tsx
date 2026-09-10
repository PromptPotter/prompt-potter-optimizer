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

// THE node-config editor. One surface, every host — a check-in authoring an origin, a fork being
// steered, a finished searchpoint being read. `mode` picks how a row is SEEDED and which value
// transport it emits on (a draft merges a whole `pipeline_overlay`; a fork seeds a sparse
// `{node:{param:value}}`), and it decides nothing the operator can see.
//
// Two rules govern what draws, both owned by `webapp/CLAUDE.md`: an AXIS is one `ui/ValueList` —
// value set, permitted subset, start value — so an enumerable row carries no padlock, `model`
// included; and a ROW answers who may SEARCH it and what WIDGET can express it, never a third
// question about whether the operator may set it.
//
// **A CHANNEL THE HOST DOES NOT PASS IS A CONTROL THAT DOES NOT DRAW** — per FACT, not per
// callback, which is what lets one component serve every host. `onApply` owns both facts at once
// (its patch carries `config` and `optimizer`), `onChange` the value alone, `onNarrowing` the
// permission alone; none of the three and the rows are text. Test the FACT (`canSetValue`,
// `narrowChannel`), never one callback: reading the tick column off `onNarrowing` would take it
// from the check-in surface, which narrows through `onApply`. `readOnly` is the separate case of a
// host that owns a channel and has it withheld.
//
// The PICKED MODEL qualifies the reasoning ladder on the MENU — a node's list is a default
// authored before anyone knew which model would run there. On the MENU only: the ticks stay the
// campaign's own declaration, because the editor emits those back.
export function NodeConfigEditor(props: {
  mode: ConfigMode;
  schema: Record<string, NodeConfigParam[]> | null;
  // How the read that produced `schema` WENT — required, and travelling beside it, because a null
  // schema is four different facts and this surface used to assert the least likely one. Not read
  // off the connector context here: two hosts resolve a schema from a different read (the
  // optimizer manifest, a draft's own response), and a status taken from the context would then
  // describe someone else's fetch.
  schemaStatus: PipelineStatus;
  // The node-config document being edited. Its ROLE differs by mode, which is why each editor
  // below receives it under its own name: search-space MERGES a patch onto it and reads no row
  // from it (`patchBase`), values SEEDS its rows from it (`valuesSeed`).
  overlay: Record<string, unknown>;
  // Whether the ACTIVE chain is one node, SERVED — never counted here. The config rows cover
  // every DECLARED node, and a check-in declares its connector's whole pipeline while running one
  // step, so counting them drew locks the engine ignores.
  isSingleNode?: boolean;
  node?: string;
  readOnly?: boolean;
  // values mode only: when false, a model the origin does not permit is held read-only —
  // steering to one is the ADR-0005 babysit act, allowed only for a principal holding
  // `campaign.babysit`. Default true keeps every other caller (draft setup, inspect)
  // unchanged; the steer form passes the operator's cap.
  babysitEditable?: boolean;
  // The half-width hosts' DENSITY (the chat run card, a measurement's run half). It tightens the
  // grid and nothing else: `compact` is never a subset, or an unmoved searchpoint folds away.
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
  // Per NODE, because this editor can span the whole pipeline: the whole-pipeline host used to
  // be a second panel that scoped itself one node at a time, and folding that back in without
  // the node id would emit one narrowing mixing every node's axes.
  onNarrowing?: (node: string, narrowing: NodeSearchNarrowing) => void;
  onChange?: (overlay: Record<string, Record<string, unknown>>) => void;
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
  } = props;
  const nodeId = node ?? "";
  // The rows as SERVED, kept beside the edited copy: `seedOverlayFromRows` needs the untouched
  // seed to tell an operator's edit from an inherited value, and diffing the live rows against
  // themselves cannot.
  const base = useMemo(
    () => configRows(schema, overlay, mode, node),
    [schema, overlay, mode, node],
  );
  const [rows, setRows] = useState<ConfigRow[]>(base);
  const [touched, setTouched] = useState<ReadonlySet<string>>(() => new Set());
  // Render-phase guarded reset. The seed lands ASYNC in the steer fork (`useRoundFile`), so an
  // edit made before it arrives must not keep masking the value it brings.
  const [prevBase, setPrevBase] = useState(base);
  if (base !== prevBase) {
    setPrevBase(base);
    setRows(base);
    setTouched(new Set());
  }

  if (rows.length === 0) {
    return <EmptyConfig status={schemaStatus} schema={schema} node={node} />;
  }

  // Each emitter fires only where its host owns the channel, and the two VALUE transports are
  // the only thing `mode` still decides: a draft merges a whole `pipeline_overlay`, a fork seeds
  // a sparse `{node:{param:value}}`. Everything above this line is one surface.
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

  // Which of the two facts this host owns. A single-node pipeline is UNLOCKABLE besides, since
  // holding its lone node would leave the optimizer nothing to tune — but its axes still narrow:
  // a tick is a permitted set, not a hold.
  const narrowChannel = Boolean(onApply || onNarrowing);
  const canLock = narrowChannel && !isSingleNode;
  const canSetValue = Boolean(onApply || onChange);

  const freeValued = rows.filter((r) => r.kind !== "model" && r.kind !== "enum");
  const nodeLocked = freeValued.length > 0 && freeValued.every((r) => r.locked);
  const toggleNodeLock = () => {
    const next = rows.map((r) =>
      r.kind === "model" || r.kind === "enum" ? r : { ...r, locked: !nodeLocked },
    );
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
    <div className={cx("config-editor", compact && "is-compact")}>
      {rows.map((r, i) => {
        if (r.kind !== "model" && r.kind !== "enum") {
          return (
            <ConfigRowView
              key={`${r.node}.${r.key}`}
              row={r}
              // The general case of the struck rungs below: a key the picked model does not
              // accept. `unsupported_params` is the SERVED answer over what we actually send, so
              // `undefined` here means the catalogue said nothing and the row claims nothing.
              ignoredBy={caps?.unsupported_params?.includes(r.key) ? pickedModel : undefined}
              readOnly={readOnly || (!babysitEditable && r.neverAxis === "cost_lever")}
              onToggleLock={canLock ? () => update(i, { locked: !r.locked }) : undefined}
              onValue={canSetValue ? (v) => update(i, { value: v }) : undefined}
            />
          );
        }
        const { values, inert, userAdded } = axisMenu(r, caps);
        // Steering the model outside what the origin permits is the ADR-0005 babysit act. Without
        // the cap those values are `inert` — the same channel a capability refusal uses, because
        // to the operator they are one fact: offered by the axis, refused downstream.
        const barred =
          r.kind === "model" && !babysitEditable
            ? values.filter((v) => !(permittedModels?.[r.node] ?? []).includes(v))
            : [];
        return (
          <div key={`${r.node}.${r.key}`} className="config-row">
            <span className="config-label" title={r.description || undefined}>
              {r.key}
              {r.fromCandidate ? (
                <span className="config-evolved" title="Carried from this searchpoint">
                  ·evolved
                </span>
              ) : null}
            </span>
            <span className="config-value">
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
            </span>
          </div>
        );
      })}

      {canLock && freeValued.length > 0 ? (
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
          axis.
          {isSingleNode
            ? " Single-node pipeline, so there is no whole-node lock: holding the only node would leave nothing to tune."
            : " 🔒 / 🔓 = held / tunable, for the params that carry no value list."}
        </small>
      ) : null}
    </div>
  );
}

// No rows is FOUR facts, and saying the last one whatever the truth is makes a read that never
// landed report a node with nothing to configure. `frontend-surface-contract.md::I1`: resolve to
// live, empty or error, never one of them wearing another's words.
function EmptyConfig({
  status,
  schema,
  node,
}: {
  status: PipelineStatus;
  schema: Record<string, NodeConfigParam[]> | null;
  node?: string;
}) {
  // Served and still empty: the node is absent from the resolution, or every param it has is a
  // prompt field — the one kind these rows subtract. The WHOLE-PIPELINE hosts pass no node, so the
  // second arm reads the flattened schema; scoped to `node` it would fall through and tell a
  // prompt-only pipeline (pp-self) that its nodes declare nothing.
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

/** The menu one axis offers, plus the two provenance facts about it — kept apart, because a value
 *  can carry both.
 *
 *  `values` unions in what is PERMITTED, so a value the operator ticked and something then refused
 *  stays on screen rather than vanishing with its permission still on the wire. The start value
 *  leads by prepend-and-filter, not by a comparator: "move one element to the front" is not a
 *  valid total order and sorts only accidentally stably.
 *
 *  `inert` = the picked MODEL refuses it, and only where the model actually answered — an UNKNOWN
 *  capability must never strike a rung. **Ticked AND struck is the intersection the engine will
 *  apply** (`param_options`), shown as the two facts it is: folding them here would let a repaint
 *  emit the model's refusals as the campaign's own narrowing. `userAdded` = neither the node nor
 *  the model offered it, so the operator typed it — which is why `available_models` is served as
 *  the ADMIN's catalogue alone. */
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
// One row of FREE-VALUED config — number, string, bool, nested. An enumerable axis is a
// `ValueList` and takes none of this chrome, in every host alike.
function ConfigRowView({
  row,
  readOnly,
  ignoredBy,
  onToggleLock,
  onValue,
}: {
  row: ConfigRow;
  readOnly: boolean;
  // The picked model, when it does NOT accept this key — so the row says the value is dropped
  // rather than showing it as a live setting. Undefined = accepted, or the catalogue never said.
  ignoredBy?: string;
  // Absent = this host does not set the padlock (a single-node pipeline, whose lone node cannot
  // be held) or the value (a permissions-only host). Each renders as what it is instead.
  onToggleLock?: () => void;
  onValue?: (v: string) => void;
}) {
  return (
    <div className="config-row">
      <span className="config-label">
        {row.key}
        {row.fromCandidate ? (
          <span className="config-evolved" title="Carried from this searchpoint">
            ·evolved
          </span>
        ) : null}
        {/* A setting the provider DROPS must not render as live: the value sits there looking
            set, the model never receives it, and nothing else says so. Wears the same badge as a
            held axis, because to a reader it is the same fact — not in play, reason in the
            title. */}
        {ignoredBy ? (
          <span
            className="config-optlocked"
            title={`${ignoredBy} does not accept ${row.key} — it is dropped by the provider, so this value has no effect.`}
          >
            ⊘
          </span>
        ) : null}
        {/* Two states — the optimizer may move this axis, or it may not — as the 🔓 / 🔒 pair the
            hint under this editor teaches. The reasons it may not are different operator remedies,
            so they ride the title with the layer names, read one row at a time. Suppressed where a
            LockButton draws beside the row: that IS the answer, and a badge repeats it. */}
        {!onToggleLock ? (
          row.movableBy.length > 0 ? (
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
          )
        ) : null}
      </span>
      <span className="config-value">
        {onToggleLock ? (
          <LockButton locked={row.locked} readOnly={readOnly} onClick={onToggleLock} />
        ) : null}
        {!onValue ? (
          // No value channel — a sibling surface sets this one, or the value is structured and
          // nothing types it. Text rather than a disabled input: a greyed box says "you may not",
          // where the truth is "not here". A nested value keeps its own line breaks, which is the
          // difference between a readable schema and one long line of JSON.
          <span className={cx("config-static", row.kind === "nested" && "is-structured")}>
            {row.value || "—"}
          </span>
        ) : row.kind === "nested" ? (
          // A box that can hold a structured value, and refuses a draft it cannot parse — the row
          // keeps what was typed instead of emitting a string over an object. `parseNested` is the
          // SAME question the emitter asks, so a box cannot accept what the emitter would drop.
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
          // Commits on Enter or blur, never per keystroke: this emission strikes a searchpoint and
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

// `neverAxis` outranks `held` (the campaign's own narrowing at mint): a key that could never be an
// axis stays that however the campaign narrowed. Both of its reasons are SERVED — the browser
// telling them apart by key name is what made every schema-owned row claim to be a cost lever.
// `model` reaches neither arm: it is an ordinary axis and reads as held or unsearched like the
// rest.
function lockReason(row: ConfigRow, readOnly: boolean): string {
  if (row.neverAxis === "schema_owned") {
    // No optimizer may emit these keys (`SCHEMA_OWNED_FIELDS`) and no fork widens that. The
    // OPERATOR sets it here like any other value: `never_axis` says who may SEARCH a key, never
    // who may set it.
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
