"use client";
import type { ReactNode } from "react";
import type { ServedBackpressure } from "@/lib/api";
import {
  PREFIX_STATE_TITLES,
  RATE_PRICED_LABEL,
  type PrefixReading,
} from "@/lib/api/types.generated";
import { SegmentedControl, Switch, Term } from "@/components/ui";
import { TERMS } from "@/lib/terms";
import { spendHeadline } from "@/lib/derivations";
import { fmtText, fmtDuration, fmtUsd, fmtTokens, PLACEHOLDER } from "@/lib/format";
import { RunLimitsControl } from "@/components/shell/remote/RunLimitsControl";
import type { RemoteModel } from "./useRemoteModel";

function providerHold(held: ServedBackpressure): string {
  const pace = held.at_once === null ? "" : ` · ${held.at_once} at once`;
  if (held.held_for_s === null) return `answering again${pace}`;
  const next = held.resumes_in_s ? `next send in ${fmtDuration(held.resumes_in_s)}` : "probing";
  return `rate-limited ${fmtDuration(held.held_for_s)} · ${next}${pace}`;
}

function cacheTag(prefix: PrefixReading): ReactNode {
  return (
    <span className="remote-spend-cache" title={PREFIX_STATE_TITLES[prefix.state]}>
      {` · ${prefix.badge}`}
    </span>
  );
}

function IdentityAndSpend({ model: m }: { model: RemoteModel }) {
  const { metered } = m;
  return (
    <div className="remote-panel-section">
      <div className="section-title">Identity</div>
      <div className="row"><span className="lbl">Unit</span><span className="val">{fmtText(m.cycleId)}</span></div>
      <div className="row"><span className="lbl">Project</span><span className="val">{fmtText(m.datasetName)}</span></div>
      <div className="row"><span className="lbl">Updated</span><span className="val">{fmtText(m.updatedAt)}</span></div>
      {m.babysat ? (
        <div className="row">
          <span className="lbl">Provenance</span>
          <Term className="val remote-babysat" content="An operator manually intervened (skip) — this cycle is no longer purely reproducible.">
            <span aria-hidden="true">✎</span> babysat
          </Term>
        </div>
      ) : null}
      <div className="section-title">Spend</div>
      {metered && m.lines.map((l) => (
        <div key={l.key} className="row">
          <span className="lbl">{l.label}</span>
          <span className="val">
            {metered.rate_known ? fmtUsd(l.kind.billed_usd) : fmtTokens(l.kind.tokens)}
            {cacheTag(l.kind.prefix)}
          </span>
        </div>
      ))}
      <div className="row"><span className="lbl">Billed</span><span className="val">{metered ? spendHeadline(metered) : PLACEHOLDER}</span></div>
      {metered?.calls_rate_priced ? (
        <div className="row"><span className="lbl">{RATE_PRICED_LABEL}</span><span className="val">{fmtUsd(metered.rate_priced_usd)}</span></div>
      ) : null}
      <div className="row"><span className="lbl">Tokens</span><span className="val">{metered ? fmtTokens(metered.billed_tokens) : PLACEHOLDER}</span></div>
      {metered?.bill_is_floor ? (
        <div className="row">
          <span className="lbl">USD cap</span>
          <Term className="val remote-spend-warn" content="USD cost couldn't be resolved for some calls (e.g. Groq returns no wire cost and the model isn't in the rate table). The $ figure undercounts real spend and the USD cap can't see it — the token cap is the backstop.">
            <span aria-hidden="true">⚠</span> inactive
          </Term>
        </div>
      ) : null}
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
      {la.money_hold !== null ? (
        <div className="row">
          <span className="lbl">Ceiling</span>
          <Term
            className="val remote-spend-warn"
            content={`Each call out holds part of the ceiling${
              la.cell_reserve_usd != null ? `, ${fmtUsd(la.cell_reserve_usd)} here` : ""
            }, so once the ceiling cannot hold another the walk runs one at a time — whatever depth is armed. Raise the budget below to widen it.`}
          >
            {la.money_hold}
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
      <span className="remote-cells-group" title={la.explained}>
        <span aria-hidden="true" className="remote-cells-icon">
          ⇉
        </span>
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
            title: d.label,
          }))}
        />
      </span>
      <div className="row">
        <span className="lbl">Every round</span>
        <span className="val">
          <Switch
            checked={la.auto}
            label="Hold as many as the stop rules allow, every round"
            locked={pending || la.auto_refusal !== ""}
            lockedNote={la.auto_refusal || "not available now"}
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
      <IdentityAndSpend model={m} />
      <SamplesInFlight model={m} />
      <div className="remote-panel-section">
        <div className="section-title">Finishing criteria</div>
        <RunLimitsControl budget={m.budget} share={m.share} metered={m.metered} />
      </div>
    </div>
  );
}
