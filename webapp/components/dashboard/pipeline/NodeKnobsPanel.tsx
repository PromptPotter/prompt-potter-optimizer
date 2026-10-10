"use client";

import { readyData, useRead } from "@/lib/hooks/useRead";
import {
  optimizerKnobsRead,
  optimizerRosterRead,
  type KnobRow,
  type ManifestNodeOverlay,
} from "@/lib/api";
import { knobRange, knobText, knobValue, parseKnob } from "@/lib/derivations";
import { useConnector } from "@/lib/hooks/useConnector";
import { useWorkspace } from "@/lib/workspace";
import { Badge, Button, CommitInput, Switch } from "@/components/ui";
import { fmtValue } from "@/lib/format";

export function NodeKnobsPanel({
  optimizer,
  nodes,
  onChange,
}: {
  optimizer?: string;
  nodes?: Record<string, ManifestNodeOverlay>;
  // A SPARSE patch: the server merges it onto the draft's overlay key by key.
  onChange?: (patch: Record<string, ManifestNodeOverlay>) => void;
} = {}) {
  const editable = onChange != null;
  const cv = useConnector();
  const { campaignId } = useWorkspace();
  const name = optimizer ?? cv.optimizer;
  const knobsRead = useRead(name ? optimizerKnobsRead(name) : null);
  const rosterRead = useRead(optimizerRosterRead());

  if (!editable && !campaignId) {
    return <p className="mech-empty">Select a campaign to see its optimizer&apos;s knobs.</p>;
  }
  if (knobsRead.status === "failed" || (!editable && cv.schema.status === "error")) {
    return <p className="mech-empty">Could not load the optimizer&apos;s knobs.</p>;
  }
  if (knobsRead.status !== "ready" || (!editable && cv.optimizerKnobs === null)) {
    return <p className="mech-empty">Loading knobs…</p>;
  }
  const menu = knobsRead.data;
  const valueOf = (node: string, k: KnobRow): unknown =>
    editable ? knobValue(nodes ?? null, node, k) : cv.optimizerKnobs?.[node]?.[k.key];
  const entry = readyData(rosterRead)?.optimizers.find((o) => o.name === menu.optimizer);
  const declared = entry?.paper ? "Paper" : "Default";

  const set = (node: string, knob: KnobRow, next: unknown) =>
    onChange!({ [node]: { config: { [knob.key]: next } } });

  if (menu.nodes.length === 0) {
    return <p className="mech-empty">{menu.optimizer} declares no knobs.</p>;
  }
  return (
    <div className="mech-groups">
      {entry?.paper ? (
        <p className="mech-lead">
          Every default below is the configuration of {entry.paper}. Change one and this run
          departs from the paper.
        </p>
      ) : null}
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
