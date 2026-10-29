"use client";

import {
  draftArmSetupRead,
  optimizerRosterRead,
  type ManifestNodeOverlay,
  type OptimizerSetup,
  type StartCheckinOptions,
} from "@/lib/api";
import { useRead } from "@/lib/hooks/useRead";
import { Button, CommitInput, ReadState, SegmentedControl } from "@/components/ui";
import { KnobItem } from "@/components/dashboard/pipeline/NodeKnobsPanel";

/** One arm as declared: what it runs unlike the setup above, and nothing else. */
export interface ArmDraft {
  // The row's identity while its key is edited; never sent.
  id: number;
  key: string;
  optimizer: string;
  // Sparse: a knob sits here only while this arm runs it unlike the setup.
  nodes: Record<string, ManifestNodeOverlay>;
}

export interface HeadToHeadDraft {
  id: string;
  arms: ArmDraft[];
}

const same = (a: unknown, b: unknown) => JSON.stringify(a) === JSON.stringify(b);

// No arm is one campaign; arms carry the launch's bound as the one budget each arm runs.
export function startOptions(launch: StartCheckinOptions, h2h: HeadToHeadDraft): StartCheckinOptions {
  if (h2h.arms.length === 0) return launch;
  return {
    head_to_head: {
      head_to_head_id: h2h.id,
      arms: h2h.arms.map((a) => ({
        arm_key: a.key,
        optimization: {
          optimizer: a.optimizer,
          ...(Object.keys(a.nodes).length > 0 ? { nodes: a.nodes } : {}),
        },
      })),
      ...(launch.budget ? { budget: launch.budget } : {}),
    },
  };
}

function nextKey(base: string, arms: ArmDraft[]): string {
  const taken = new Set(arms.map((a) => a.key));
  let key = base;
  for (let n = 2; taken.has(key); n += 1) key = `${base}-${n}`;
  return key;
}

// *patch* laid on the arm, kept sparse: a knob set back to the setup's value leaves the arm.
function moved(arm: ArmDraft, patch: Record<string, ManifestNodeOverlay>, setup: OptimizerSetup) {
  let nodes = arm.nodes;
  for (const [node, { config }] of Object.entries(patch)) {
    const rows = setup.knobs.find((g) => g.node === node)?.knobs;
    const held = Object.entries({ ...nodes[node]?.config, ...config }).filter(([key, value]) => {
      const row = rows?.find((k) => k.key === key);
      return row === undefined || !same(row.value, value);
    });
    const rest = Object.entries(nodes).filter(([n]) => n !== node);
    nodes = Object.fromEntries(
      held.length > 0 ? [...rest, [node, { config: Object.fromEntries(held) }]] : rest,
    );
  }
  return { ...arm, nodes };
}

function ArmKnobs({
  arm,
  setup,
  onArm,
}: {
  arm: ArmDraft;
  setup: OptimizerSetup;
  onArm: (arm: ArmDraft) => void;
}) {
  const count = Object.values(arm.nodes).reduce((n, o) => n + Object.keys(o.config).length, 0);
  return (
    <details className="mech-all">
      <summary>
        Knobs <small>{count === 0 ? "as the setup runs them" : `${count} moved`}</small>
      </summary>
      <ul className="mech-list">
        {setup.knobs.flatMap((g) =>
          g.knobs.map((knob) => {
            const own = arm.nodes[g.node]?.config;
            const runs = own !== undefined && knob.key in own ? own[knob.key] : knob.value;
            return (
              <KnobItem
                key={`${g.node}.${knob.key}`}
                node={g.node}
                knob={{ ...knob, declared: knob.value, value: runs }}
                declaredLabel="the setup's"
                showNode
                onChange={(patch) => onArm(moved(arm, patch, setup))}
              />
            );
          }),
        )}
      </ul>
    </details>
  );
}

// The setup the arm's own optimizer runs over this draft, whichever the setup above names.
function ArmSetup({
  draftId,
  arm,
  onArm,
}: {
  draftId: string;
  arm: ArmDraft;
  onArm: (arm: ArmDraft) => void;
}) {
  const setupRead = useRead(draftArmSetupRead(draftId, arm.optimizer));
  return (
    <ReadState read={setupRead} subject={`the knobs ${arm.optimizer} runs`} className="mech-empty">
      {(setup) => <ArmKnobs arm={arm} setup={setup} onArm={onArm} />}
    </ReadState>
  );
}

export function HeadToHeadArms({
  value,
  onChange,
  optimizers,
  base,
  draftId,
}: {
  value: HeadToHeadDraft;
  onChange: (next: HeadToHeadDraft) => void;
  // The roster's names, in its order.
  optimizers: string[];
  // The optimizer the setup above runs: a new arm's.
  base: string;
  draftId: string;
}) {
  const setArm = (i: number, arm: ArmDraft) =>
    onChange({ ...value, arms: value.arms.map((a, j) => (j === i ? arm : a)) });
  return (
    <div className="mech-groups">
      <p className="mech-lead">
        With no arm, Start runs one campaign. Each arm is a campaign of its own from this origin,
        all under one head-to-head and each bound by the run bounds, so their results compare.
      </p>
      {value.arms.length > 0 ? (
        <div className="mech-row-head">
          <span className="mech-row-label">Head-to-head</span>
          <CommitInput
            className="mech-knob-input"
            aria-label="Head-to-head id"
            value={value.id}
            onCommit={(id) => onChange({ ...value, id })}
          />
        </div>
      ) : null}
      <ol className="mech-list">
        {value.arms.map((arm, i) => (
          <li key={arm.id} className="mech-row" role="group" aria-label={`Arm ${arm.key}`}>
            <div className="mech-row-head">
              <CommitInput
                className="mech-knob-input"
                aria-label="Arm key"
                value={arm.key}
                onCommit={(key) => setArm(i, { ...arm, key })}
              />
              <Button
                variant="ghost"
                onClick={() => onChange({ ...value, arms: value.arms.filter((_, j) => j !== i) })}
              >
                Remove
              </Button>
            </div>
            <SegmentedControl<string>
              options={optimizers.map((o) => ({ value: o, label: o }))}
              value={arm.optimizer}
              onChange={(optimizer) => setArm(i, { ...arm, optimizer, nodes: {} })}
              ariaLabel="Optimizer"
            />
            <ArmSetup draftId={draftId} arm={arm} onArm={(next) => setArm(i, next)} />
          </li>
        ))}
      </ol>
      <Button
        onClick={() =>
          onChange({
            ...value,
            arms: [
              ...value.arms,
              {
                id: Math.max(0, ...value.arms.map((a) => a.id)) + 1,
                key: nextKey(base, value.arms),
                optimizer: base,
                nodes: {},
              },
            ],
          })
        }
      >
        Add arm
      </Button>
    </div>
  );
}

export function HeadToHeadSection(props: Omit<Parameters<typeof HeadToHeadArms>[0], "optimizers">) {
  const rosterRead = useRead(optimizerRosterRead());
  return (
    <ReadState read={rosterRead} subject="the optimizers this install runs" className="mech-empty">
      {(roster) => <HeadToHeadArms {...props} optimizers={roster.optimizers.map((o) => o.name)} />}
    </ReadState>
  );
}
