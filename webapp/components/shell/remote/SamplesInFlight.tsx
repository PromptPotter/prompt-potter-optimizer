import { type ServedBackpressure, LOOKAHEAD_WORDS } from "@/lib/api/types";
import { Said, SegmentedControl, Switch, Term } from "@/components/ui";
import { TERMS } from "@/lib/terms";
import {
  affordableText,
  lookaheadDepthHint,
  lookaheadDepthLabel,
  lookaheadFlight,
  lookaheadScope,
  lookaheadShallow,
} from "@/lib/derivations";
import { fmtDuration, fmtUsd } from "@/lib/format";
import type { RemoteModel } from "./useRemoteModel";

function providerHold(held: ServedBackpressure): string {
  const pace = held.at_once === null ? "" : ` · ${held.at_once} at once`;
  if (held.held_for_s === null) return `answering again${pace}`;
  const next = held.resumes_in_s ? `next send in ${fmtDuration(held.resumes_in_s)}` : "probing";
  return `rate-limited ${fmtDuration(held.held_for_s)} · ${next}${pace}`;
}

export function SamplesInFlight({ model: m }: { model: RemoteModel }) {
  const { lookahead: la, providerHeld } = m;
  const pending = m.pending !== null;
  const shallow = lookaheadShallow(la);
  const scope = lookaheadScope(la);
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
      {m.heldCall ? (
        <div className="row">
          <span className="lbl">Walk</span>
          <Said
            className="val"
            tone={m.heldCall.tone}
            label={m.heldCall.label}
            sub={m.heldCall.sub}
            detail={m.heldCall.detail}
          />
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
      <div className="remote-cells-group">
        <span className="lbl">Parallel calls</span>
        <SegmentedControl
          ariaLabel={`Parallel calls: how many to hold in flight, up to ${la.pick_max}`}
          className="remote-cells"
          value={String(la.pressed)}
          onChange={m.armCells}
          options={la.depths.map((d) => ({
            value: String(d.cells),
            label: lookaheadDepthLabel(d, la),
            fill: d.fill ?? undefined,
            disabled: pending || !d.pickable,
            hint: lookaheadDepthHint(d, la),
          }))}
        />
        <p className="remote-note">{lookaheadFlight(la)}</p>
        {shallow !== null ? <p className="remote-note remote-note-why">{shallow}</p> : null}
      </div>
      <div className="row">
        <span className="lbl">Every round</span>
        <span className="val">
          <Switch
            checked={la.auto}
            label="Keep this depth every round"
            locked={pending || la.auto_refusal !== null}
            lockedNote={la.auto_refusal === null ? "saving" : LOOKAHEAD_WORDS[la.auto_refusal]}
            onChange={() => m.arm(la.pressed, !la.auto)}
          />
        </span>
      </div>
      {scope !== null ? <p className="remote-note">{scope}</p> : null}
    </div>
  );
}
