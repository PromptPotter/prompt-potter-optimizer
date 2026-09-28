"use client";
// An optimizer's node knobs: editable given `nodes` + `onChange` (a check-in's draft), else the
// viewed course's, read-only. Rows come from `GET /optimizers/{name}/knobs`, so a knob a manifest
// adds appears here with no edit; each row names the manifest's value, a paper preset's being its paper's.

import { readyData, useRead } from "@/lib/hooks/useRead";
import {
  fetchCampaignDetail,
  fetchOptimizerKnobs,
  fetchOptimizerRoster,
  type KnobRow,
} from "@/lib/api";
import { knobText, knobValue, parseKnob, type OptimizerNodeOverlay } from "@/lib/derivations";
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
  nodes?: OptimizerNodeOverlay;
  // A SPARSE patch — one knob — merged server-side onto the draft's overlay key by key.
  onChange?: (patch: OptimizerNodeOverlay) => void;
} = {}) {
  const editable = onChange != null;
  const cv = useConnector();
  const { campaignId } = useWorkspace();
  const detailRead = useRead(
    !editable && campaignId
      ? { key: campaignId, fetch: (signal) => fetchCampaignDetail(campaignId, signal) }
      : null,
    { surface: "campaign-detail" },
  );
  const name = optimizer ?? cv.optimizer;
  const knobsRead = useRead(
    name ? { key: name, fetch: (signal) => fetchOptimizerKnobs(name, signal) } : null,
    { surface: "optimizer-knobs" },
  );
  const rosterRead = useRead(
    { key: "optimizers", fetch: (signal) => fetchOptimizerRoster(signal) },
    { surface: "optimizer-roster" },
  );

  if (!editable && !campaignId) {
    return <p className="mech-empty">Select a campaign to see its optimizer&apos;s knobs.</p>;
  }
  if (knobsRead.status === "failed" || detailRead.status === "failed") {
    return <p className="mech-empty">Could not load the optimizer&apos;s knobs.</p>;
  }
  if (knobsRead.status !== "ready" || detailRead.status === "loading") {
    return <p className="mech-empty">Loading knobs…</p>;
  }
  const menu = knobsRead.data;
  const detail = readyData(detailRead);
  const values: OptimizerNodeOverlay | null = editable
    ? (nodes ?? null)
    : ((detail?.config.optimization as { nodes?: OptimizerNodeOverlay } | undefined)?.nodes ??
      null);
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
      {/* Never a card: both hosts already draw a border. */}
      {menu.nodes.map((group) => (
        <section key={group.node} className="mech-group">
          <h3 className="mech-card-title">{group.node}</h3>
          <p className="mech-group-desc">{group.kind}</p>
          <ul className="mech-list">
            {group.knobs.map((k) => {
              const value = knobValue(values, group.node, k);
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
  // A list or an object: JSON, and text that does not parse never commits.
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
