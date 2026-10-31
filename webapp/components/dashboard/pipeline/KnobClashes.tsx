"use client";

import { readyData, useRead } from "@/lib/hooks/useRead";
import { configMapRead, type ConfigCoupling } from "@/lib/api";
import { COUPLING_SEVERITY_INFO } from "@/lib/api/types.generated";
import { useWorkspace } from "@/lib/workspace";
import { Badge, ReadState } from "@/components/ui";

// The couplings this campaign's config VIOLATES — the preflight's own check, so no surface
// disagrees with the engine. A coupling the config keeps is no news and is not listed.
export function useKnobClashes(): ConfigCoupling[] {
  const { campaignId } = useWorkspace();
  const read = useRead(campaignId ? configMapRead(campaignId) : null);
  return readyData(read)?.couplings.filter((c) => c.active) ?? [];
}

export function KnobClashes() {
  const { campaignId } = useWorkspace();
  const read = useRead(campaignId ? configMapRead(campaignId) : null);
  return (
    <ReadState read={read} subject="the knob clashes" idle={null}>
      {(map) => {
        const active = map.couplings.filter((c) => c.active);
        if (active.length === 0) return null;
        return (
          <section className="knob-clashes" aria-label="Knob clashes">
            <h3 className="mech-card-title">Clashes in this config</h3>
            <ul className="knob-clash-list">
              {active.map((c) => (
                <li key={c.name} className="knob-clash">
                  <div className="knob-clash-head">
                    <Badge tone={COUPLING_SEVERITY_INFO[c.severity].tone}>{c.severity}</Badge>
                    <span className="knob-clash-knobs">{c.labels.join(" × ")}</span>
                  </div>
                  <p className="knob-clash-consequence">{c.consequence}</p>
                  <p className="knob-clash-relation">{c.relation}</p>
                </li>
              ))}
            </ul>
          </section>
        );
      }}
    </ReadState>
  );
}
