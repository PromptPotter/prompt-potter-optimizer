import { Term } from "@/components/ui";
import { couponRead } from "@/lib/api";
import { cx } from "@/lib/cx";
import { allowanceCaveats, billText } from "@/lib/derivations";
import { cssPct } from "@/lib/format";
import { shownData, useRead } from "@/lib/hooks/useRead";

export function AccountSpend() {
  const read = useRead(couponRead());

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
  const fill = data.used_share;
  const said = data.headline;

  const explain = (
    <div className="account-spend-explain">
      <p>
        <strong>{billText(host.billed_usd, host.bill_is_floor)}</strong> billed on the host&rsquo;s
        keys and <strong>{billText(own.billed_usd, own.bill_is_floor)}</strong> on your own, across
        every campaign this account has run, deleted ones included.
      </p>
      {said.detail ? <p>{said.detail}</p> : null}
      {said.sub ? <p>{said.sub}</p> : null}
      {allowanceCaveats(host).map((line) => (
        <p key={line}>{line}</p>
      ))}
    </div>
  );

  return (
    <div className={cx("account-spend", said.tone === "warn" && "account-spend-exhausted")}>
      <span className="account-spend-label">{said.label}</span>
      <Term className="account-spend-reading" content={explain}>
        <span className="account-spend-value">{said.value}</span>
      </Term>
      {fill !== null ? (
        <span className="account-spend-bar" aria-hidden="true">
          <span style={{ inlineSize: cssPct(fill) }} />
        </span>
      ) : null}
    </div>
  );
}
