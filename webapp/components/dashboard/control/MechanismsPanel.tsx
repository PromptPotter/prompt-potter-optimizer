"use client";
// Pluggable orchestration mechanisms (campaign.json::optimization.mechanisms).
// ONE component, two modes — the same element the operator asked to reuse:
//   • pass `mechanisms` + `onChange` → EDITABLE (new-campaign form, Switch);
//     each flip sends the full object up (like origin_prompt_fields).
//   • omit them → READ-ONLY (dashboard): self-fetches the in-view committed
//     campaign's config and renders Badge ON/OFF.
// Group/toggle structure, labels, descriptions, and defaults come from the
// `MechanismConfig` schema (`GET /campaigns/mechanisms-schema`), so a toggle
// added backend-side appears in both modes with no edit here.

import { useFetch } from "@/lib/hooks/useFetch";
import { fetchCampaignDetail, fetchMechanismsSchema } from "@/lib/api";
import { useWorkspace } from "@/lib/workspace";
import { CardFrame, Badge, Switch } from "@/components/ui";

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
  const { data: schema } = useFetch(() => fetchMechanismsSchema(), []);
  // Committed-config source — only the read-only (dashboard) mode needs it.
  const { data: detail } = useFetch(
    !editable && campaignId ? (signal) => fetchCampaignDetail(campaignId, signal) : null,
    [editable, campaignId],
  );

  if (!schema) return <p className="mech-empty">Loading mechanisms…</p>;
  if (!editable && !campaignId) {
    return <p className="mech-empty">Select a campaign to see its mechanism toggles.</p>;
  }

  const opt = (detail?.config.optimization ?? {}) as Record<string, unknown>;
  const values: MechanismValues | null = editable
    ? (mechanisms ?? null)
    : ((opt.mechanisms as MechanismValues | undefined) ?? null);

  // Editable flip rebuilds the FULL nested object from current values (filling
  // defaults from the schema) so the draft patch always validates server-side.
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
      {schema.groups.map((group) => (
        <CardFrame
          key={group.key}
          className="mech-card"
          title={<span className="mech-card-title">{group.label}</span>}
        >
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
                          overridden ? `Overridden — default ${t.default ? "ON" : "OFF"}` : "Default"
                        }
                      >
                        {on ? "ON" : "OFF"}
                        {overridden ? " •" : ""}
                      </Badge>
                    )}
                  </div>
                  <p className="mech-row-desc">{t.description}</p>
                </li>
              );
            })}
          </ul>
        </CardFrame>
      ))}
    </div>
  );
}
