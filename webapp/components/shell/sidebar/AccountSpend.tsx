"use client";
import { useEffect, useRef } from "react";
import { Term } from "@/components/ui";
import { couponRead } from "@/lib/api";
import { COUPON_POLL_MS } from "@/lib/cadence";
import { cx } from "@/lib/cx";
import { allowanceCaveats, billText, couponFaceText, couponLeftText } from "@/lib/derivations";
import { cssPct } from "@/lib/format";
import { shownData, useRead } from "@/lib/hooks/useRead";
import { invalidateReads } from "@/lib/read-cache";
import { useRegistry } from "@/lib/state/registry";

export function AccountSpend() {
  const { entries } = useRegistry();
  const read = useRead(couponRead(), { auth: true, intervalMs: COUPON_POLL_MS });

  const signature = entries.map((c) => c.facts.spend_lifetime.billed_usd).join(",");
  const asked = useRef(signature);
  useEffect(() => {
    if (asked.current === signature) return;
    asked.current = signature;
    invalidateReads("coupon");
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

  const host = data.host_key_spend;
  const own = data.own_key_spend;
  // A retired coupon serves `exhausted` too, but nothing is wrong: the account sends on its own keys.
  const exhausted = !data.host && !data.void && data.exhausted;
  const fill = data.used_share;

  const explain = (
    <div className="account-spend-explain">
      <p>
        <strong>{billText(host.billed_usd, host.bill_is_floor)}</strong> billed on the host&rsquo;s
        keys and <strong>{billText(own.billed_usd, own.bill_is_floor)}</strong> on your own, across
        every campaign this account has run, deleted ones included.
      </p>
      <p>
        {data.host
          ? "No allowance applies: this account runs the box, on its own provider keys."
          : `The host's allowance for the life of the account: ${couponLeftText(data)} (${couponFaceText(data)}). Your own keys never draw it — add one under Account → API keys.`}
      </p>
      {allowanceCaveats(host).map((line) => (
        <p key={line}>{line}</p>
      ))}
    </div>
  );

  return (
    <div className={cx("account-spend", exhausted && "account-spend-exhausted")}>
      <span className="account-spend-label">
        {data.host
          ? "Spend"
          : data.void
            ? "Allowance retired"
            : exhausted
              ? "Allowance used up"
              : "Allowance"}
      </span>
      <span className="account-spend-cap">{couponFaceText(data)}</span>
      <Term className="account-spend-reading" content={explain}>
        <span className="account-spend-value">{billText(host.billed_usd, host.bill_is_floor)}</span>
      </Term>
      {fill !== null ? (
        <span className="account-spend-bar" aria-hidden="true">
          <span style={{ inlineSize: cssPct(fill) }} />
        </span>
      ) : null}
    </div>
  );
}
