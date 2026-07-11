"use client";
// Config map (campaign.json::optimization knobs) — the operator-facing half of
// the coupling registry: which knob moves which statistical estimand, what
// overwrites what, and which knobs currently CLASH. Read-only: self-fetches the
// in-view campaign's `GET /campaigns/{id}/config-map`, server-authored from the
// one `application/knobs` registry (same source as the CLI diagnostic + the
// pre-run preflight warning), so this panel never disagrees with the engine on
// which knobs collide.

import { useFetch } from "@/lib/hooks/useFetch";
import { fetchConfigMap, type ConfigCoupling } from "@/lib/api";
import { useWorkspace } from "@/lib/workspace";
import { CardFrame, Badge, type BadgeTone } from "@/components/ui";

const SEVERITY_TONE: Record<string, BadgeTone> = {
  collision: "danger",
  inert: "accent",
  info: "default",
};

function sourceTone(source: string): BadgeTone {
  if (source === "campaign" || source === "required") return "accent";
  return "default"; // default | constant — muted
}

function fmtValue(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "boolean") return value ? "ON" : "OFF";
  if (Array.isArray(value)) return value.length === 0 ? "[]" : JSON.stringify(value);
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

function CouplingRow({ c }: { c: ConfigCoupling }) {
  return (
    <li className="cmap-coupling" data-active={c.active}>
      <div className="cmap-coupling-head">
        <Badge tone={c.active ? SEVERITY_TONE[c.severity] : "default"}>
          {c.active ? `clash · ${c.severity}` : c.severity}
        </Badge>
        <span className="cmap-coupling-knobs">{c.labels.join("  ×  ")}</span>
      </div>
      <p className="cmap-coupling-relation">{c.relation}</p>
      {c.active ? <p className="cmap-coupling-consequence">→ {c.consequence}</p> : null}
    </li>
  );
}

export function ConfigMapPanel() {
  const { campaignId } = useWorkspace();
  const { data: map } = useFetch(
    campaignId ? (signal) => fetchConfigMap(campaignId, signal) : null,
    [campaignId],
  );

  if (!campaignId) {
    return <p className="mech-empty">Select a campaign to see its config map.</p>;
  }
  if (!map) return <p className="mech-empty">Loading config map…</p>;

  // Active clashes first, then by severity weight, so the collision (if any) leads.
  const order = ["collision", "inert", "info"];
  const couplings = [...map.couplings].sort((a, b) => {
    if (a.active !== b.active) return a.active ? -1 : 1;
    return order.indexOf(a.severity) - order.indexOf(b.severity);
  });
  const active = map.couplings.filter((c) => c.active);
  const hasCollision = active.some((c) => c.severity === "collision");

  return (
    <div className="cmap">
      <CardFrame
        className="cmap-card"
        title={<span className="mech-card-title">Couplings — what clashes with what</span>}
      >
        {active.length === 0 ? (
          <p className="cmap-clear">No knob clashes in this config.</p>
        ) : (
          <p className="cmap-summary" data-collision={hasCollision}>
            <Badge tone={hasCollision ? "danger" : "accent"}>
              {active.length} active clash{active.length === 1 ? "" : "es"}
            </Badge>{" "}
            {hasCollision
              ? "a collision makes a statistical quantity ill-defined — read the consequence before flipping."
              : "tuned knobs that no-op or co-move — not a soundness bug, but worth seeing."}
          </p>
        )}
        <ul className="cmap-couplings">
          {couplings.map((c) => (
            <CouplingRow key={c.name} c={c} />
          ))}
        </ul>
      </CardFrame>

      <CardFrame
        className="cmap-card"
        title={<span className="mech-card-title">Knobs by statistical estimand</span>}
      >
        <p className="mech-group-desc">
          Every knob grouped by the quantity it moves, with its effective value and the layer that
          value came from — <em>campaign</em> (operator-set), <em>required</em>, <em>constant</em>
          (a hardcoded floor), or a muted default.
        </p>
        <div className="cmap-estimands">
          {map.groups.map((g) => (
            <div key={g.key} className="cmap-estimand">
              <div className="cmap-estimand-head">
                <span className="cmap-estimand-name">{g.label}</span>
                <span className="cmap-estimand-doc">{g.doc}</span>
              </div>
              <ul className="cmap-knobs">
                {g.knobs.map((k) => (
                  <li key={k.path} className="cmap-knob">
                    <span className="cmap-knob-label" title={k.path}>
                      {k.label}
                    </span>
                    <span className="cmap-knob-value">{fmtValue(k.value)}</span>
                    {k.source === "default" ? (
                      <span className="cmap-knob-source-default">default</span>
                    ) : (
                      <Badge tone={sourceTone(k.source)}>{k.source}</Badge>
                    )}
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>
      </CardFrame>
    </div>
  );
}
