"use client";

import { useState } from "react";
import { readyData, useRead } from "@/lib/hooks/useRead";
import {
  optimizerRosterRead,
  type KnobRow,
  type ManifestNodeOverlay,
  type OptimizerRoster,
  type OptimizerSetup,
  type PoolFill,
  type PoolOffer,
  type PoolRow,
} from "@/lib/api";
import { knobRange, knobText, parseKnob } from "@/lib/derivations";
import { promptField, promptFieldLabel } from "@/lib/prompt-fields";
import { useConnector } from "@/lib/hooks/useConnector";
import { useWorkspace } from "@/lib/workspace";
import {
  Badge,
  Button,
  Chip,
  ChipGroup,
  CommitInput,
  Empty,
  FailureLine,
  Loading,
  Switch,
  Term,
} from "@/components/ui";
import { cx } from "@/lib/cx";
import { fmtValue } from "@/lib/format";

type OnChange = ((patch: Record<string, ManifestNodeOverlay>) => void) | undefined;

// The entry chips a pool shows before its "show all": a demo pool's split runs to dozens.
const ENTRIES_INLINE = 8;
const ENTRIES_FIRST = 10;

export function NodeKnobsPanel({
  onChange,
}: {
  // A SPARSE patch: the server merges it onto the draft's overlay key by key.
  onChange?: (patch: Record<string, ManifestNodeOverlay>) => void;
} = {}) {
  const editable = onChange != null;
  const cv = useConnector();
  const { campaignId } = useWorkspace();
  const rosterRead = useRead(optimizerRosterRead());
  const setup = cv.optimizerSetup;

  if (!editable && !campaignId) {
    return <Empty className="mech-empty">Select a campaign to see its optimizer&apos;s knobs.</Empty>;
  }
  if (cv.schema.status === "error") {
    return <Empty className="mech-empty">Could not load the optimizer&apos;s knobs.</Empty>;
  }
  if (setup === null) {
    return <Loading className="mech-empty">Reading the optimizer&apos;s knobs…</Loading>;
  }
  return (
    <KnobGroups
      optimizer={cv.optimizer}
      setup={setup}
      entry={readyData(rosterRead)?.optimizers.find((o) => o.name === cv.optimizer)}
      onChange={onChange}
    />
  );
}

// One member's knobs as `OptimizerSetup` serves them — the node detail's reading, never `node_config_schema`.
export function MemberKnobList({ node }: { node: string }) {
  const cv = useConnector();
  const rosterRead = useRead(optimizerRosterRead());
  // A member `knobs` does not list declares none.
  const group = cv.optimizerSetup?.knobs.find((g) => g.node === node);
  if (group === undefined || group.knobs.length === 0) return null;
  const knobs = group.knobs;
  const entry = readyData(rosterRead)?.optimizers.find((o) => o.name === cv.optimizer);
  return (
    <section className="mech-member" aria-label={`${node} knobs`}>
      <span className="prompt-editor-title">Knobs</span>
      <ul className="mech-list">
        {knobs.map((k) => (
          <KnobItem
            key={k.key}
            node={node}
            knob={k}
            declaredLabel={declaredLabel(entry)}
            onChange={undefined}
          />
        ))}
      </ul>
    </section>
  );
}

function declaredLabel(entry: OptimizerRoster["optimizers"][number] | undefined): string {
  return entry?.paper ? "paper" : "default";
}

function KnobGroups({
  optimizer,
  setup,
  entry,
  onChange,
}: {
  optimizer: string | null;
  setup: OptimizerSetup;
  entry: OptimizerRoster["optimizers"][number] | undefined;
  onChange: OnChange;
}) {
  const declared = declaredLabel(entry);
  const isMain = (node: string, key: string) =>
    setup.main.some((ref) => ref.node === node && ref.knob === key);
  // Served in `main`'s order; the server refuses a manifest whose `main` names no member's knob.
  const main = setup.main.flatMap((ref) => {
    const row = setup.knobs.find((g) => g.node === ref.node)?.knobs.find((k) => k.key === ref.knob);
    return row ? [{ node: ref.node, knob: row }] : [];
  });
  const rest = setup.knobs
    .map((g) => ({ ...g, knobs: g.knobs.filter((k) => !isMain(g.node, k.key)) }))
    .filter((g) => g.knobs.length > 0);

  if (setup.knobs.length === 0) {
    return <Empty className="mech-empty">{optimizer ?? "This optimizer"} declares no knobs.</Empty>;
  }
  return (
    <div className="mech-groups">
      {entry?.paper ? (
        <p className="mech-lead">
          Every default below is the configuration of {entry.paper}. Change one and this run
          departs from the paper.
        </p>
      ) : null}
      {main.length > 0 ? (
        <section className="mech-group">
          <h3 className="mech-card-title">Main knobs</h3>
          <ul className="mech-list">
            {main.map(({ node, knob }) => (
              <KnobItem
                key={`${node}.${knob.key}`}
                node={node}
                knob={knob}
                declaredLabel={declared}
                showNode
                onChange={onChange}
              />
            ))}
          </ul>
        </section>
      ) : null}
      <section className="mech-group">
        <h3 className="mech-card-title">Tested each round</h3>
        <p className="mech-group-desc">
          <span className="mech-num">{setup.arms_per_round}</span> arm
          {setup.arms_per_round === 1 ? "" : "s"}:{" "}
          {setup.proposers.map((p, i) => (
            <span key={p.node}>
              {i > 0 ? " · " : null}
              <Term
                content={
                  <p className="mech-card-text">
                    Made by the <code className="mech-code">{p.node}</code> node.
                  </p>
                }
              >
                {p.makes}
              </Term>
            </span>
          ))}
        </p>
        <CandidateBorrows pools={setup.pools} onChange={onChange} />
      </section>
      {rest.length > 0 ? (
        <details className="mech-all">
          <summary>
            All knobs <small>{rest.reduce((n, g) => n + g.knobs.length, 0)}</small>
          </summary>
          {rest.map((group) => (
            <section key={group.node} className="mech-group">
              <h3 className="mech-card-title">
                <code className="mech-code">{group.node}</code>
                <small className="mech-row-node">{group.kind}</small>
              </h3>
              <ul className="mech-list">
                {group.knobs.map((k) => (
                  <KnobItem
                    key={k.key}
                    node={group.node}
                    knob={k}
                    declaredLabel={declared}
                    onChange={onChange}
                  />
                ))}
              </ul>
            </section>
          ))}
        </details>
      ) : null}
    </div>
  );
}

// `declared` is what "Changed from" and Reset name: the manifest's here, the setup's on a head-to-head arm.
export function KnobItem({
  node,
  knob: k,
  declaredLabel,
  showNode = false,
  onChange,
}: {
  node: string;
  knob: KnobRow;
  declaredLabel: string;
  showNode?: boolean;
  onChange: OnChange;
}) {
  const changed = JSON.stringify(k.value) !== JSON.stringify(k.declared);
  const set = onChange ? (next: unknown) => onChange({ [node]: { config: { [k.key]: next } } }) : null;
  const range = knobRange(k);
  // The whole text, and the range, behind the name: the row itself carries one served line.
  const more = k.description !== k.summary || range !== null;
  const name = <span className="mech-row-key">{k.key}</span>;
  return (
    <li className="mech-row">
      <div className="mech-row-head">
        <span className="mech-row-label">
          {more ? (
            <Term
              content={
                <>
                  <p className="mech-card-text">{k.description}</p>
                  {range ? <p className="mech-card-meta">Range {range}</p> : null}
                </>
              }
            >
              {name}
            </Term>
          ) : (
            name
          )}
          {showNode ? <span className="mech-row-node">{node}</span> : null}
        </span>
        {set ? (
          <KnobControl knob={k} value={k.value} onSet={set} />
        ) : (
          <Badge tone={k.value === true ? "success" : "default"}>{fmtValue(k.value)}</Badge>
        )}
      </div>
      {k.summary ? <p className="mech-row-desc">{k.summary}</p> : null}
      {changed ? (
        <p className="mech-row-default">
          <span>
            Changed from {declaredLabel} {fmtValue(k.declared)}
          </span>
          {set ? (
            <Button variant="ghost" className="mech-reset" onClick={() => set(k.declared)}>
              Reset
            </Button>
          ) : null}
        </p>
      ) : null}
    </li>
  );
}

// What a pool's entries are called, by where they come from.
const POOL_NOUN: Record<PoolRow["source"], { one: string; many: string; inPlay: string }> = {
  library: { one: "snippet", many: "snippets", inPlay: "Snippets in play" },
  partition: { one: "row", many: "rows", inPlay: "Rows in play" },
};

function counted(n: number, noun: { one: string; many: string }): string {
  return `${n} ${n === 1 ? noun.one : noun.many}`;
}

// Each pool is drawn at the arm's prompt parts it fills; one serving neither a fill nor a pack renders nothing.
function CandidateBorrows({ pools, onChange }: { pools: PoolRow[]; onChange: OnChange }) {
  const shown = pools.filter(
    (p) => p.fills.length > 0 || p.packs.length > 0 || p.refused.length > 0,
  );
  if (shown.length === 0) return null;
  return (
    <div className="mech-borrow">
      <p className="mech-borrow-lead">Each arm&apos;s prompt may borrow</p>
      {shown.map((pool) => (
        <PoolInPlace key={pool.pool} pool={pool} onChange={onChange} />
      ))}
    </div>
  );
}

function PoolInPlace({ pool, onChange }: { pool: PoolRow; onChange: OnChange }) {
  const noun = POOL_NOUN[pool.source];
  const named = pool.packs.filter((p) => p.on).map((p) => p.name);
  const setPacks = onChange
    ? (packs: string[]) => onChange({ [pool.initializer]: { config: { packs } } })
    : undefined;
  const closed = pool.drawn_by.some((d) => d.access === "closed");
  return (
    <div className="mech-borrow-pool">
      {pool.packs.length > 0 ? (
        <div className="mech-borrow-packs">
          <Term
            content={
              <p className="mech-card-text">
                Libraries of prompt phrasings, installed here or banked from your own edits. The
                optimizer is shown what a pack holds for each section below; phrasings earlier runs
                on this kind of answer earned join them when the run starts.
              </p>
            }
          >
            {named.length === 0 ? "Add phrasings from a pack" : "Phrasings from"}
          </Term>
          <ChipGroup label="Snippet packs">
            {pool.packs.map((p) => (
              <Chip
                key={p.name}
                on={p.on}
                disabled={!setPacks}
                onClick={() =>
                  setPacks?.(p.on ? named.filter((n) => n !== p.name) : [...named, p.name])
                }
              >
                <span className="mech-offer">
                  {p.name} <small>{p.entries}</small>
                </span>
              </Chip>
            ))}
          </ChipGroup>
        </div>
      ) : null}
      {/* A refused pack serves nothing, so it sits beside the chips it would have been. */}
      {pool.refused.map((r) => (
        <FailureLine key={r.name} lead={`Pack ${r.name} refused:`} failure={{ message: r.reason }} />
      ))}
      {pool.fills.length > 0 ? (
        <ul className="mech-fills" aria-label={`Where ${noun.many} land`}>
          {pool.fills.map((f) => {
            const { label: part, hint } = promptField(f.part);
            return (
              <li key={f.part} className="mech-fill">
                <span className="mech-fill-part">
                  {hint ? <Term content={hint}>{part}</Term> : part}
                </span>
                <Term className="mech-fill-count" content={fillTeaching(pool, f, part, closed)}>
                  {f.most === null
                    ? counted(f.entries, noun)
                    : `up to ${f.most} of ${counted(f.entries, noun)}`}
                </Term>
              </li>
            );
          })}
        </ul>
      ) : null}
      {pool.offered.length > 0 ? (
        <details className="mech-fold">
          <summary>Pick {noun.many}</summary>
          <PoolPicker
            pool={pool}
            onActive={
              onChange
                ? (active) => onChange({ [pool.initializer]: { config: { active } } })
                : undefined
            }
          />
          <ul className="mech-pool-readers" aria-label="Read by">
            {pool.drawn_by.map((d) => (
              <li key={`${d.switch.node}.${d.switch.knob}`}>
                <span className="mech-pool-access">{d.access}</span>
                <span>{d.nodes.join(", ")}</span>
                <small className="mech-row-node">
                  set by {d.switch.node}.{d.switch.knob}
                </small>
              </li>
            ))}
          </ul>
        </details>
      ) : null}
    </div>
  );
}

function fillTeaching(pool: PoolRow, f: PoolFill, part: string, closed: boolean): string {
  if (pool.source === "partition") {
    const cap = f.most === null ? "some" : `up to ${f.most}`;
    return `Rows of your data held out of scoring. The optimizer may attach ${cap} to an arm's prompt as worked examples, each with its answer.`;
  }
  return closed
    ? `An arm's ${part.toLowerCase()} is one of these phrasings, word for word: the optimizer picks one and never writes its own.`
    : `When the optimizer rewrites an arm's ${part.toLowerCase()}, it may reuse one of these phrasings as is, adapt one, or write its own.`;
}

// A lit chip is a path in play, lit from the served `active`; the last one stays lit, since a pool keeps at least one.
function PoolPicker({
  pool,
  onActive,
}: {
  pool: PoolRow;
  onActive: ((active: string[]) => void) | undefined;
}) {
  const noun = POOL_NOUN[pool.source];
  const [all, setAll] = useState(false);
  const toggle = (path: string) =>
    onActive?.(
      pool.active.includes(path) ? pool.active.filter((p) => p !== path) : [...pool.active, path],
    );
  const chip = (o: PoolOffer) => {
    const on = pool.active.includes(o.path);
    return (
      <Chip
        key={o.path}
        on={on}
        disabled={!onActive || (on && pool.active.length === 1)}
        onClick={() => toggle(o.path)}
      >
        <span
          className={cx(
            "mech-offer",
            (o.kind === "part" || o.kind === "source") && "is-group",
            o.kind === "entry" && "is-entry",
          )}
        >
          {o.kind === "part" ? promptFieldLabel(o.label) : o.label}
          {o.kind === "entry" ? null : <> <small>{o.entries}</small></>}
        </span>
      </Chip>
    );
  };
  const groups = pool.offered.filter((o) => o.kind !== "entry");
  const entries = pool.offered.filter((o) => o.kind === "entry");
  const { inPlay } = noun;
  if (entries.length <= ENTRIES_INLINE) {
    return <ChipGroup label={inPlay}>{pool.offered.map(chip)}</ChipGroup>;
  }
  const shown = all ? entries : entries.slice(0, ENTRIES_FIRST);
  return (
    <>
      <ChipGroup label={inPlay}>{groups.map(chip)}</ChipGroup>
      <details className="mech-fold">
        <summary>
          {counted(entries.length, noun)}, one by one
          {entries.some((o) => pool.active.includes(o.path))
            ? ` · ${entries.filter((o) => pool.active.includes(o.path)).length} picked`
            : ""}
        </summary>
        <ChipGroup label={`${inPlay}, one by one`}>{shown.map(chip)}</ChipGroup>
        {entries.length > shown.length ? (
          <Button variant="ghost" onClick={() => setAll(true)}>
            Show all {entries.length}
          </Button>
        ) : null}
      </details>
    </>
  );
}

function KnobControl({
  knob,
  value,
  onSet,
}: {
  knob: KnobRow;
  value: unknown;
  onSet: (next: unknown) => void;
}) {
  if (knob.type === "boolean") {
    return <Switch checked={value === true} label={knob.key} onChange={() => onSet(value !== true)} />;
  }
  if (knob.options) {
    return (
      <select aria-label={knob.key} value={String(value)} onChange={(e) => onSet(e.target.value)}>
        {knob.options.map((o) => (
          <option key={o} value={o}>
            {o}
          </option>
        ))}
      </select>
    );
  }
  const commits = (draft: string) => parseKnob(knob, draft) !== undefined;
  const commit = (draft: string) => onSet(parseKnob(knob, draft));
  if (knob.type === "integer" || knob.type === "number") {
    return (
      <CommitInput
        className="mech-knob-input"
        aria-label={knob.key}
        inputMode={knob.type === "integer" ? "numeric" : "decimal"}
        placeholder={knob.nullable ? "off" : undefined}
        value={knobText(value)}
        validate={commits}
        onCommit={commit}
      />
    );
  }
  if (knob.type === "string") {
    return (
      <CommitInput
        className="mech-knob-input"
        aria-label={knob.key}
        value={knobText(value)}
        validate={commits}
        onCommit={commit}
      />
    );
  }
  return (
    <CommitInput
      className="mech-knob-input mech-knob-json"
      aria-label={`${knob.key} (JSON)`}
      rows={2}
      value={knobText(value)}
      validate={commits}
      onCommit={commit}
    />
  );
}
