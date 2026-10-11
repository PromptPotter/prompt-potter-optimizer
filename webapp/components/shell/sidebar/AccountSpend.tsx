"use client";
import { useEffect, useRef } from "react";
import { Term } from "@/components/ui";
import { quotaRead } from "@/lib/api";
import { cx } from "@/lib/cx";
import { billText, ratePricedText } from "@/lib/derivations";
import { fmtTokens, fmtUsd } from "@/lib/format";
import { shownData, useRead } from "@/lib/hooks/useRead";
import { invalidateReads } from "@/lib/read-cache";
import { useRegistry } from "@/lib/registry";

// The quota read sums every ledger the account owns: polled slowly, re-ticked when spend moves.
const QUOTA_POLL_MS = 60_000;

export function AccountSpend() {
  const { campaigns } = useRegistry();
  const read = useRead(quotaRead(), { auth: true, intervalMs: QUOTA_POLL_MS });

  const signature = campaigns.map((c) => c.spend_lifetime.billed_usd).join(",");
  const asked = useRef(signature);
  useEffect(() => {
    if (asked.current === signature) return;
    asked.current = signature;
    invalidateReads("quota");
  }, [signature]);

  if (read.status === "idle") return null;
  const data = shownData(read);
  if (data == null) {
    return (
      <div className="account-spend" aria-busy={read.status === "loading"}>
        <span className="account-spend-label">Spend</span>
        <span className="account-spend-muted">
          {read.status === "loading" ? "…" : "unavailable"}
        </span>
      </div>
    );
  }

  const cap = data.spend_budget_usd_total;
  const lifetime = data.spend_lifetime;
  const floor = lifetime.bill_is_floor;
  const spent = fmtUsd(lifetime.billed_usd);
  const priced = ratePricedText(lifetime.rate_priced_usd, lifetime.calls_rate_priced);
  const exhausted = data.allowance_spent;
  const fill = data.spend_budget_used_share;

  const explain = (
    <div className="account-spend-explain">
      <p>
        <strong>{floor ? `At least ${spent}` : spent}</strong> of real model spend across every
        campaign this account has run, deleted ones included.
      </p>
      <p>
        {cap === null
          ? "No allowance applies: this account runs on the box's own provider key."
          : `The allowance is ${fmtUsd(cap)} for the life of the account${exhausted ? ", and it is spent — every result stays readable." : "."}`}
      </p>
      {floor ? (
        <p>
          {fmtTokens(lifetime.unpriced_tokens)} were billed by a model with no known price, so
          the dollar figure undercounts and the token allowance is the one holding.
        </p>
      ) : null}
      {priced ? (
        <p>
          {priced} beside it — calls no provider reported a bill for. Not spent; the allowance
          counts it anyway.
        </p>
      ) : null}
      {lifetime.sends_unreported ? (
        <p>
          Up to {fmtUsd(lifetime.unreported_usd)} more is unreported — sends that ended with no
          bill (cancelled, timed out, killed). Not spent, unknown; the allowance holds it anyway.
        </p>
      ) : null}
    </div>
  );

  return (
    <div className={cx("account-spend", exhausted && "account-spend-exhausted")}>
      <span className="account-spend-label">{exhausted ? "Allowance spent" : "Spend"}</span>
      <span className="account-spend-cap">
        {cap === null ? "no allowance" : `of ${fmtUsd(cap)}`}
      </span>
      <Term className="account-spend-reading" content={explain}>
        <span className="account-spend-value">
          {billText(lifetime.billed_usd, floor)}
        </span>
      </Term>
      {fill !== null ? (
        <span className="account-spend-bar" aria-hidden="true">
          <span style={{ inlineSize: `${fill * 100}%` }} />
        </span>
      ) : null}
    </div>
  );
}
