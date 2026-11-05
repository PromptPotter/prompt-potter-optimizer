import { readyData, useRead } from "@/lib/hooks/useRead";
import type { ReadResult } from "@/lib/read-cache";
import { configMapRead } from "@/lib/api";
import {
  type ConfigCoupling,
  type ConfigMapResponse,
  COUPLING_SEVERITY_INFO,
} from "@/lib/api/types";
import { useWorkspace } from "@/lib/workspace";
import { Badge, ReadState } from "@/components/ui";

// The couplings this campaign's config VIOLATES — the preflight's own check, so no surface
// disagrees with the engine. A coupling the config keeps is no news and is not listed. `active` is
// null until the read lands: a failed read is no clash count, never zero.
export function useKnobClashes(): {
  read: ReadResult<ConfigMapResponse>;
  active: ConfigCoupling[] | null;
} {
  const { campaignId } = useWorkspace();
  const read = useRead(campaignId ? configMapRead(campaignId) : null);
  return { read, active: readyData(read)?.couplings.filter((c) => c.active) ?? null };
}

export function KnobClashes() {
  const { read } = useKnobClashes();
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
