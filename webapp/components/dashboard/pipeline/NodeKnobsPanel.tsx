"use client";

import { readyData, useRead } from "@/lib/hooks/useRead";
import {
  optimizerRosterRead,
  type KnobRow,
  type ManifestNodeOverlay,
  type OptimizerRoster,
  type OptimizerSetup,
  type PoolOffer,
  type PoolRow,
} from "@/lib/api";
import { knobRange, knobText, parseKnob } from "@/lib/derivations";
import { useConnector } from "@/lib/hooks/useConnector";
import { useWorkspace } from "@/lib/workspace";
import { Badge, Button, Chip, ChipGroup, CommitInput, Empty, Loading, Switch } from "@/components/ui";
import { cx } from "@/lib/cx";
import { fmtValue } from "@/lib/format";

type OnChange = ((patch: Record<string, ManifestNodeOverlay>) => void) | undefined;

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
  const declared = entry?.paper ? "Paper" : "Default";
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
      <div className="mech-pools">
        <section className="mech-pools-col">
          <h3 className="mech-card-title">Tested each round</h3>
          <p className="mech-group-desc">{setup.arms_per_round} arms, made by</p>
          <ul className="mech-list">
            {setup.proposers.map((node) => (
              <li key={node} className="mech-row-label">
                {node}
              </li>
            ))}
          </ul>
        </section>
        <section className="mech-pools-col">
          <h3 className="mech-card-title">Draws from</h3>
          {setup.pools.length === 0 ? (
            <p className="mech-group-desc">No pool: every arm is written fresh.</p>
          ) : (
            <ul className="mech-list">
              {setup.pools.map((pool) => (
                <PoolItem key={pool.pool} pool={pool} onChange={onChange} />
              ))}
            </ul>
          )}
        </section>
      </div>
      {rest.length > 0 ? (
        <details className="mech-all">
          <summary>All knobs</summary>
          {rest.map((group) => (
            <section key={group.node} className="mech-group">
              <h3 className="mech-card-title">{group.node}</h3>
              <p className="mech-group-desc">{group.kind}</p>
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

function KnobItem({
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
  return (
    <li className="mech-row">
      <div className="mech-row-head">
        <span className="mech-row-label">
          {k.key}
          {showNode ? <code className="mech-row-node">{node}</code> : null}
        </span>
        {set ? (
          <KnobControl knob={k} value={k.value} onSet={set} />
        ) : (
          <Badge tone={k.value === true ? "success" : "default"}>{fmtValue(k.value)}</Badge>
        )}
      </div>
      <p className="mech-row-desc">{k.description}</p>
      <p className="mech-row-default">
        {declaredLabel} {fmtValue(k.declared)}
        {knobRange(k) ? ` · range ${knobRange(k)}` : ""}
        {changed ? " · changed" : ""}
        {set && changed ? (
          <Button variant="ghost" onClick={() => set(k.declared)}>
            Reset
          </Button>
        ) : null}
      </p>
    </li>
  );
}

function PoolItem({ pool, onChange }: { pool: PoolRow; onChange: OnChange }) {
  return (
    <li className="mech-row">
      <div className="mech-row-head">
        <span className="mech-row-label">{pool.pool}</span>
        <Badge>{pool.source}</Badge>
      </div>
      <PoolPicker
        pool={pool}
        onActive={
          onChange ? (active) => onChange({ [pool.initializer]: { config: { active } } }) : undefined
        }
      />
      <ul className="mech-pool-readers">
        {pool.drawn_by.map((d) => (
          <li key={`${d.switch.node}.${d.switch.knob}`} className="mech-row-head">
            <span>{d.nodes.join(", ")}</span>
            <Badge>{d.access}</Badge>
            <code className="mech-row-desc">
              {d.switch.node}.{d.switch.knob}
            </code>
          </li>
        ))}
      </ul>
    </li>
  );
}

function offerText(o: PoolOffer): string {
  return o.kind === "entry" ? "entry" : `${o.kind} · ${o.entries}`;
}

// A lit chip is a path in play, lit from the served `active`; the last one stays lit, since a pool keeps at least one.
function PoolPicker({
  pool,
  onActive,
}: {
  pool: PoolRow;
  onActive: ((active: string[]) => void) | undefined;
}) {
  if (pool.offered.length === 0) {
    return <p className="mech-row-desc">Its entries are drawn when the run starts.</p>;
  }
  const toggle = (path: string) =>
    onActive?.(
      pool.active.includes(path) ? pool.active.filter((p) => p !== path) : [...pool.active, path],
    );
  return (
    <ChipGroup label={`${pool.pool} entries in play`}>
      {pool.offered.map((o) => {
        const on = pool.active.includes(o.path);
        return (
          <Chip
            key={o.path}
            on={on}
            disabled={!onActive || (on && pool.active.length === 1)}
            onClick={() => toggle(o.path)}
          >
            <span className={cx("mech-offer", o.kind === "group" && "is-group", o.kind === "entry" && "is-entry")}>
              {o.label} <small>{offerText(o)}</small>
            </span>
          </Chip>
        );
      })}
    </ChipGroup>
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
