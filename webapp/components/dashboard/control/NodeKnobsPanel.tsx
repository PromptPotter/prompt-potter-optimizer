"use client";
// An optimizer's node knobs: editable given `nodes` + `onChange`, else the viewed campaign's,
// read-only. Rows come from `GET /optimizers/{name}/knobs`, so a knob a manifest adds appears here
// with no edit; a boolean or a closed set is a control, any other type is shown, never typed in.

import { readyData, useRead } from "@/lib/hooks/useRead";
import { fetchCampaignDetail, fetchOptimizerKnobs, type KnobRow } from "@/lib/api";
import { useWorkspace } from "@/lib/workspace";
import { Badge, Switch } from "@/components/ui";
import { fmtValue } from "@/lib/format";

export type NodeOverlay = Record<string, { config: Record<string, unknown> }>;

// The frozen snapshot is a delta from defaults, so an absent `optimizer` is `OptimizationConfig`'s.
const DEFAULT_OPTIMIZER = "potter";

function valueOf(nodes: NodeOverlay | null, node: string, knob: KnobRow): unknown {
  const set = nodes?.[node]?.config;
  return set && knob.key in set ? set[knob.key] : knob.value;
}

export function NodeKnobsPanel({
  optimizer,
  nodes,
  onChange,
}: {
  optimizer?: string;
  nodes?: NodeOverlay;
  onChange?: (next: NodeOverlay) => void;
} = {}) {
  const editable = onChange != null;
  const { campaignId } = useWorkspace();
  const detailRead = useRead(
    !editable && campaignId
      ? { key: campaignId, fetch: (signal) => fetchCampaignDetail(campaignId, signal) }
      : null,
    { surface: "campaign-detail" },
  );
  const detail = readyData(detailRead);
  const opt = (detail?.config.optimization ?? {}) as {
    optimizer?: string;
    nodes?: NodeOverlay;
  };
  const name = optimizer ?? opt.optimizer ?? DEFAULT_OPTIMIZER;
  const knobsRead = useRead(
    { key: name, fetch: (signal) => fetchOptimizerKnobs(name, signal) },
    { surface: "optimizer-knobs" },
  );

  if (knobsRead.status === "failed" || detailRead.status === "failed") {
    return <p className="mech-empty">Could not load the optimizer&apos;s knobs.</p>;
  }
  if (knobsRead.status !== "ready" || detailRead.status === "loading") {
    return <p className="mech-empty">Loading knobs…</p>;
  }
  if (!editable && !campaignId) {
    return <p className="mech-empty">Select a campaign to see its optimizer&apos;s knobs.</p>;
  }
  const menu = knobsRead.data;
  const values: NodeOverlay | null = editable ? (nodes ?? null) : (opt.nodes ?? null);

  // Only what differs from the manifest is written, so the overlay stays the campaign's delta.
  const set = (node: string, knob: KnobRow, next: unknown) => {
    const out: NodeOverlay = structuredClone(values ?? {});
    const config = { ...(out[node]?.config ?? {}) };
    if (next === knob.value) delete config[knob.key];
    else config[knob.key] = next;
    if (Object.keys(config).length) out[node] = { config };
    else delete out[node];
    onChange!(out);
  };

  return (
    <div className="mech-groups">
      {/* Never a card: both hosts already draw a border. */}
      {menu.nodes.map((group) => (
        <section key={group.node} className="mech-group">
          <h3 className="mech-card-title">{group.node}</h3>
          <p className="mech-group-desc">{group.kind}</p>
          <ul className="mech-list">
            {group.knobs.map((k) => {
              const value = valueOf(values, group.node, k);
              const overridden = JSON.stringify(value) !== JSON.stringify(k.value);
              return (
                <li key={k.key} className="mech-row">
                  <div className="mech-row-head">
                    <span className="mech-row-label">{k.key}</span>
                    {editable && k.type === "boolean" ? (
                      <Switch
                        checked={value === true}
                        label={k.key}
                        onChange={() => set(group.node, k, value !== true)}
                      />
                    ) : editable && k.options ? (
                      <select
                        aria-label={k.key}
                        value={String(value)}
                        onChange={(e) => set(group.node, k, e.target.value)}
                      >
                        {k.options.map((o) => (
                          <option key={o} value={o}>
                            {o}
                          </option>
                        ))}
                      </select>
                    ) : (
                      <Badge
                        tone={value === true ? "success" : "default"}
                        title={
                          overridden ? `Overridden — manifest ${fmtValue(k.value)}` : "Manifest"
                        }
                      >
                        {fmtValue(value)}
                        {overridden ? " •" : ""}
                      </Badge>
                    )}
                  </div>
                  <p className="mech-row-desc">{k.description}</p>
                </li>
              );
            })}
          </ul>
        </section>
      ))}
    </div>
  );
}
