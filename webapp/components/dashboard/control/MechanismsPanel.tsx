"use client";
// Orchestration mechanisms: editable given `mechanisms` + `onChange`, else read-only. Structure
// comes from `GET /campaigns/mechanisms-schema`, so a backend toggle appears with no edit here.

import { readyData, useRead } from "@/lib/hooks/useRead";
import { fetchCampaignDetail, fetchMechanismsSchema } from "@/lib/api";
import { useWorkspace } from "@/lib/workspace";
import { Badge, Switch } from "@/components/ui";
import { fmtValue } from "@/lib/format";

type MechanismValues = Record<string, Record<string, boolean>>;

function valueOf(values: MechanismValues | null, g: string, k: string, fallback: boolean): boolean {
  const v = values?.[g]?.[k];
  return typeof v === "boolean" ? v : fallback;
}

export function MechanismsPanel({
  mechanisms,
  onChange,
}: {
  mechanisms?: MechanismValues;
  onChange?: (next: MechanismValues) => void;
} = {}) {
  const editable = onChange != null;
  const { campaignId } = useWorkspace();
  const schemaRead = useRead(
    { key: "mechanisms-schema", fetch: fetchMechanismsSchema },
    { surface: "mechanisms-schema" },
  );
  const detailRead = useRead(
    !editable && campaignId
      ? { key: campaignId, fetch: (signal) => fetchCampaignDetail(campaignId, signal) }
      : null,
    { surface: "campaign-detail" },
  );
  const detail = readyData(detailRead);

  if (schemaRead.status === "failed" || detailRead.status === "failed") {
    return <p className="mech-empty">Could not load the mechanism toggles.</p>;
  }
  if (schemaRead.status !== "ready" || detailRead.status === "loading") {
    return <p className="mech-empty">Loading mechanisms…</p>;
  }
  const schema = schemaRead.data;
  if (!editable && !campaignId) {
    return <p className="mech-empty">Select a campaign to see its mechanism toggles.</p>;
  }

  const opt = (detail?.config.optimization ?? {}) as Record<string, unknown>;
  const values: MechanismValues | null = editable
    ? (mechanisms ?? null)
    : ((opt.mechanisms as MechanismValues | undefined) ?? null);

  // Rebuild the FULL nested object, schema defaults filled, so the draft patch validates.
  const flip = (g: string, k: string, next: boolean) => {
    const full: MechanismValues = {};
    for (const group of schema.groups) {
      full[group.key] = Object.fromEntries(
        group.toggles.map((t) => [
          t.key,
          group.key === g && t.key === k ? next : valueOf(values, group.key, t.key, t.default),
        ]),
      );
    }
    onChange!(full);
  };

  return (
    <div className="mech-groups">
      {/* Never a card: both hosts already draw a border. */}
      {schema.groups.map((group) => (
        <section key={group.key} className="mech-group">
          <h3 className="mech-card-title">{group.label}</h3>
          <p className="mech-group-desc">{group.description}</p>
          <ul className="mech-list">
            {group.toggles.map((t) => {
              const on = valueOf(values, group.key, t.key, t.default);
              const overridden = on !== t.default;
              return (
                <li key={t.key} className="mech-row">
                  <div className="mech-row-head">
                    <span className="mech-row-label">{t.label}</span>
                    {editable ? (
                      <Switch
                        checked={on}
                        label={t.label}
                        onChange={() => flip(group.key, t.key, !on)}
                      />
                    ) : (
                      <Badge
                        tone={on ? "success" : "default"}
                        title={
                          overridden ? `Overridden — default ${fmtValue(t.default)}` : "Default"
                        }
                      >
                        {fmtValue(on)}
                        {overridden ? " •" : ""}
                      </Badge>
                    )}
                  </div>
                  <p className="mech-row-desc">{t.description}</p>
                </li>
              );
            })}
          </ul>
        </section>
      ))}
    </div>
  );
}
