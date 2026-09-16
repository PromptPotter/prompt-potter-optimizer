"use client";
import { useState } from "react";
import { Term } from "@/components/ui";
import { failureKind, fetchQuotaStatus, type FailureKind, type QuotaStatus } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import { cx } from "@/lib/cx";
import { fmtTokens, fmtUsd } from "@/lib/format";
import { usePoll } from "@/lib/hooks/usePoll";
import { useRevalidation } from "@/lib/revalidate";
import { useWorkspace } from "@/lib/workspace";

// The quota read sums every ledger the account owns, so it polls slowly and re-ticks early when
// the campaign list's SERVED spend moves — a round that bills is what changes the total.
const QUOTA_POLL_MS = 60_000;

// What this ACCOUNT has spent, against its ALLOWANCE — pinned to the sidebar in every state,
// the collapsed rail included. Both numbers are `/auth/quota-status`'s.
export function AccountSpend() {
  const { status } = useAuth();
  const { campaigns } = useWorkspace();
  const generation = useRevalidation();
  const authed = status === "authed";
  const [data, setData] = useState<QuotaStatus | null>(null);
  const [failure, setFailure] = useState<FailureKind | null>(null);

  const signature = campaigns.map((c) => c.spend_used_usd).join(",");
  const [moved, setMoved] = useState({ signature, count: 0 });
  if (signature !== moved.signature) setMoved({ signature, count: moved.count + 1 });

  usePoll(
    (signal) =>
      fetchQuotaStatus(signal).then(
        (q) => {
          setData(q);
          setFailure(null);
        },
        (e: unknown) => {
          if (!signal.aborted) setFailure(failureKind(e));
        },
      ),
    { intervalMs: QUOTA_POLL_MS, enabled: authed, revalidateOn: generation + moved.count },
  );

  if (!authed) return null;
  // The last good reading stays up through a failed re-read; only a first read can fail visibly.
  if (data == null) {
    return (
      <div className="account-spend" aria-busy={failure == null}>
        <span className="account-spend-label">Spend</span>
        <span className="account-spend-muted">{failure == null ? "…" : "unavailable"}</span>
      </div>
    );
  }

  const cap = data.spend_budget_usd_total;
  const floor = data.spend_unpriced_tokens > 0;
  const spent = fmtUsd(data.spend_used_total_usd);
  const exhausted = cap !== null && data.spend_used_total_usd >= cap;
  const fill = cap === null || cap <= 0 ? null : Math.min(1, data.spend_used_total_usd / cap);

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
          {fmtTokens(data.spend_unpriced_tokens)} were billed by a model with no known price, so
          the dollar figure undercounts and the token allowance is the one holding.
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
          {floor ? "≥" : ""}
          {spent}
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
