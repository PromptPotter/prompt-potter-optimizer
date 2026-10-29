"use client";

import { readyData, useRead } from "@/lib/hooks/useRead";
import {
  optimizerKnobsRead,
  optimizerRosterRead,
  type KnobRow,
  type ManifestNodeOverlay,
  type OptimizerKnobsResponse,
  type OptimizerRoster,
  type OptimizerSetup,
  type PoolRow,
} from "@/lib/api";
import { knobRange, knobText, parseKnob } from "@/lib/derivations";
import { useConnector } from "@/lib/hooks/useConnector";
import { useWorkspace } from "@/lib/workspace";
import {
  Badge,
  Button,
  Chip,
  ChipGroup,
  CommitInput,
  Empty,
  Loading,
  ReadState,
  Switch,
} from "@/components/ui";
import { fmtValue } from "@/lib/format";

export function NodeKnobsPanel({
  onChange,
}: {
  // A SPARSE patch: the server merges it onto the draft's overlay key by key.
  onChange?: (patch: Record<string, ManifestNodeOverlay>) => void;
} = {}) {
  const editable = onChange != null;
  const cv = useConnector();
  const { campaignId } = useWorkspace();
  const knobsRead = useRead(cv.optimizer ? optimizerKnobsRead(cv.optimizer) : null);
  const rosterRead = useRead(optimizerRosterRead());
  const setup = cv.optimizerSetup;

  if (!editable && !campaignId) {
    return <Empty className="mech-empty">Select a campaign to see its optimizer&apos;s knobs.</Empty>;
  }
  if (cv.schema.status === "error") {
    return <Empty className="mech-empty">Could not load the optimizer&apos;s knobs.</Empty>;
  }
  const reading = <Loading className="mech-empty">Reading the optimizer&apos;s knobs…</Loading>;
  return (
    <ReadState read={knobsRead} subject="the optimizer’s knobs" className="mech-empty" idle={reading}>
      {(menu) =>
        setup === null ? (
          reading
        ) : (
          <KnobGroups
            menu={menu}
            setup={setup}
            entry={readyData(rosterRead)?.optimizers.find((o) => o.name === menu.optimizer)}
            onChange={onChange}
          />
        )
      }
    </ReadState>
  );
}

function KnobGroups({
  menu,
  setup,
  entry,
  onChange,
}: {
  menu: OptimizerKnobsResponse;
  setup: OptimizerSetup;
  entry: OptimizerRoster["optimizers"][number] | undefined;
  onChange: ((patch: Record<string, ManifestNodeOverlay>) => void) | undefined;
}) {
  const editable = onChange != null;
  const valueOf = (node: string, k: KnobRow): unknown => setup.knobs[node]?.[k.key];
  const declared = entry?.paper ? "Paper" : "Default";

  const set = (node: string, knob: KnobRow, next: unknown) =>
    onChange!({ [node]: { config: { [knob.key]: next } } });

  if (menu.nodes.length === 0) {
    return <Empty className="mech-empty">{menu.optimizer} declares no knobs.</Empty>;
  }
  return (
    <div className="mech-groups">
      {entry?.paper ? (
        <p className="mech-lead">
          Every default below is the configuration of {entry.paper}. Change one and this run
          departs from the paper.
        </p>
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
                <li key={pool.pool} className="mech-row">
                  <div className="mech-row-head">
                    <span className="mech-row-label">{pool.pool}</span>
                    <Badge>{fmtValue(pool.active)}</Badge>
                  </div>
                  {onChange ? (
                    <PoolPicker
                      pool={pool}
                      onActive={(active) => onChange({ [pool.initializer]: { config: { active } } })}
                    />
                  ) : null}
                  <ul className="mech-pool-readers">
                    {pool.drawn_by.map((d) => (
                      <li key={d.node} className="mech-row-head">
                        <span>{d.node}</span>
                        <Badge>{d.access}</Badge>
                        <span className="mech-row-desc">
                          {d.switch.node}.{d.switch.knob}
                        </span>
                      </li>
                    ))}
                  </ul>
                </li>
              ))}
            </ul>
          )}
        </section>
      </div>
      {menu.nodes.map((group) => (
        <section key={group.node} className="mech-group">
          <h3 className="mech-card-title">{group.node}</h3>
          <p className="mech-group-desc">{group.kind}</p>
          <ul className="mech-list">
            {group.knobs.map((k) => {
              const value = valueOf(group.node, k);
              const changed = JSON.stringify(value) !== JSON.stringify(k.value);
              return (
                <li key={k.key} className="mech-row">
                  <div className="mech-row-head">
                    <span className="mech-row-label">{k.key}</span>
                    {editable ? (
                      <KnobControl knob={k} value={value} onSet={(v) => set(group.node, k, v)} />
                    ) : (
                      <Badge tone={value === true ? "success" : "default"}>{fmtValue(value)}</Badge>
                    )}
                  </div>
                  <p className="mech-row-desc">{k.description}</p>
                  <p className="mech-row-default">
                    {declared} {fmtValue(k.value)}
                    {knobRange(k) ? ` · range ${knobRange(k)}` : ""}
                    {changed ? " · changed" : ""}
                    {editable && changed ? (
                      <Button variant="ghost" onClick={() => set(group.node, k, k.value)}>
                        Reset
                      </Button>
                    ) : null}
                  </p>
                </li>
              );
            })}
          </ul>
        </section>
      ))}
    </div>
  );
}

// A lit chip is a path in play; the last one stays lit, since a pool keeps at least one.
function PoolPicker({ pool, onActive }: { pool: PoolRow; onActive: (active: string[]) => void }) {
  const offered = Object.entries(pool.offered);
  if (offered.length === 0) {
    return <p className="mech-row-desc">Its entries are drawn when the run starts.</p>;
  }
  const toggle = (path: string) =>
    onActive(
      pool.active.includes(path) ? pool.active.filter((p) => p !== path) : [...pool.active, path],
    );
  return (
    <ChipGroup label={`${pool.pool} entries in play`}>
      {offered.map(([path, entries]) => {
        const on = pool.active.includes(path);
        return (
          <Chip
            key={path}
            on={on}
            disabled={on && pool.active.length === 1}
            onClick={() => toggle(path)}
          >
            {path} · {entries}
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
