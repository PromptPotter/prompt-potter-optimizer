"use client";
import type { ServedBackpressure } from "@/lib/api";
import { SegmentedControl, Switch, Term } from "@/components/ui";
import { TERMS } from "@/lib/terms";
import { LOOKAHEAD_WORDS } from "@/lib/api/types.generated";
import { affordableText, lookaheadDepthLabel, lookaheadExplained } from "@/lib/derivations";
import { fmtDuration, fmtUsd } from "@/lib/format";
import { RunLimitsControl } from "@/components/shell/remote/RunLimitsControl";
import type { RemoteModel } from "./useRemoteModel";

function providerHold(held: ServedBackpressure): string {
  const pace = held.at_once === null ? "" : ` · ${held.at_once} at once`;
  if (held.held_for_s === null) return `answering again${pace}`;
  const next = held.resumes_in_s ? `next send in ${fmtDuration(held.resumes_in_s)}` : "probing";
  return `rate-limited ${fmtDuration(held.held_for_s)} · ${next}${pace}`;
}

// The panel says what the masthead does not: whether an operator's hand is on this cycle.
function Provenance() {
  return (
    <div className="remote-panel-section">
      <div className="section-title">Provenance</div>
      <div className="row">
        <span className="lbl">Intervened</span>
        <Term className="val remote-babysat" content="An operator manually intervened (skip) — this cycle is no longer purely reproducible.">
          <span aria-hidden="true">✎</span> babysat
        </Term>
      </div>
    </div>
  );
}

function SamplesInFlight({ model: m }: { model: RemoteModel }) {
  const { lookahead: la, providerHeld } = m;
  const pending = m.pending !== null;
  return (
    <div className="remote-panel-section">
      <div className="section-title">Samples in flight</div>
      <Term className="row" content={TERMS.remote_flight}>
        <span className="lbl">{la.scoring_now ? "Now" : "Next round"}</span>
        <span className="val">
          {la.scoring_now
            ? `${la.in_flight} out · ${la.allowed} allowed · ${la.most} at most`
            : `${la.most} at most`}
        </span>
      </Term>
      {m.waitNote ? (
        <div className="row">
          <span className="lbl">Held by</span>
          <span className="val remote-spend-warn">{m.waitNote}</span>
        </div>
      ) : null}
      {la.affordable !== null ? (
        <div className="row">
          <span className="lbl">Ceiling</span>
          <Term
            className="val remote-spend-warn"
            content={`Each call out holds part of the ceiling${
              la.cell_reserve_usd != null ? `, ${fmtUsd(la.cell_reserve_usd)} here` : ""
            }, so once the ceiling cannot hold another the walk runs one at a time — whatever depth is armed. Raise the budget below to widen it.`}
          >
            {affordableText(la.affordable)}
          </Term>
        </div>
      ) : null}
      {providerHeld ? (
        <div className="row">
          <span className="lbl">Provider</span>
          <Term className="val remote-spend-warn" content={providerHeld.detail}>
            {providerHold(providerHeld)}
          </Term>
        </div>
      ) : null}
      <span className="remote-cells-group">
        <Term className="remote-cells-icon" content={lookaheadExplained(la)}>
          ⇉
        </Term>
        <SegmentedControl
          ariaLabel={`Calls to hold in flight, up to ${la.pick_max}`}
          className="remote-cells"
          value={String(la.depth)}
          onChange={m.armCells}
          options={la.depths.map((d) => ({
            value: String(d.cells),
            label: String(d.cells),
            fill: d.fill ?? undefined,
            disabled: pending || !d.pickable,
            title: lookaheadDepthLabel(d),
          }))}
        />
      </span>
      <div className="row">
        <span className="lbl">Every round</span>
        <span className="val">
          <Switch
            checked={la.auto}
            label="Hold as many as the stop rules allow, every round"
            locked={pending || la.auto_refusal !== null}
            lockedNote={la.auto_refusal === null ? "saving" : LOOKAHEAD_WORDS[la.auto_refusal]}
            onChange={() => m.arm(la.depth, !la.auto)}
          />
        </span>
      </div>
    </div>
  );
}

export function RemotePanel({ model: m }: { model: RemoteModel }) {
  return (
    <div className="remote-panel" role="region" aria-label="Job status and configuration">
      {m.babysat && <Provenance />}
      <SamplesInFlight model={m} />
      <div className="remote-panel-section">
        <div className="section-title">Finishing criteria</div>
        <RunLimitsControl budget={m.budget} share={m.share} metered={m.metered} />
      </div>
    </div>
  );
}
