"use client";
// Security pane — sign-in provider badge + quota status + sign-out.

import { useState } from "react";
import { PROVIDER_LABEL } from "./providers";
import { cx } from "@/lib/cx";
import { fmtTokens, fmtUsd } from "@/lib/format";
import { useFetch } from "@/lib/hooks/useFetch";
import {
  fetchQuotaStatus,
  postLogout,
  type MeResponse,
  type QuotaStatus,
} from "@/lib/api";

export function AccountSecurityTab({ me }: { me: MeResponse }) {
  const { data: quota, error: quotaError } = useFetch(() => fetchQuotaStatus(), []);
  const [signOutError, setSignOutError] = useState<string | null>(null);
  const [signingOut, setSigningOut] = useState(false);
  // Either failure mode surfaces in the same banner; the quota read is the hook's.
  const error = quotaError ?? signOutError;

  const handleSignOut = async () => {
    setSigningOut(true);
    try {
      await postLogout();
      window.location.href = "/login";
    } catch (e) {
      setSignOutError(String(e));
      setSigningOut(false);
    }
  };

  return (
    <>
      <div className="account-row">
        <span className="account-label">Sign-in provider</span>
        <div className="account-row-main">
          <span className="account-badge account-badge-strong">
            {me.provider ? PROVIDER_LABEL[me.provider] ?? me.provider : "—"}
          </span>
        </div>
      </div>
      <div className="account-row">
        <span className="account-label">Quota status</span>
        <div className="account-row-main">
          {error ? <p className="account-error">{error}</p> : null}
          {!quota && !error ? <p className="account-muted">Loading…</p> : null}
          {quota ? <QuotaCard quota={quota} /> : null}
        </div>
      </div>
      <div className="account-row">
        <span className="account-label">Session</span>
        <div className="account-row-main">
          <button
            type="button"
            className="account-signout"
            onClick={handleSignOut}
            disabled={signingOut}
          >
            {signingOut ? "Signing out…" : "Sign out"}
          </button>
        </div>
      </div>
    </>
  );
}

function QuotaCell({
  label,
  value,
  sub,
  warn,
}: {
  label: string;
  value: string;
  sub: string;
  warn?: string;
}) {
  return (
    <li className="quota-cell">
      <span className="quota-cell-label">{label}</span>
      <span className="quota-cell-value">{value}</span>
      <span className={cx("quota-cell-sub", warn && "quota-cell-warn")} title={warn}>
        {sub}
      </span>
    </li>
  );
}

const ofTotal = (cap: number | null, fmt: (n: number) => string) =>
  cap === null ? "uncapped" : `of ${fmt(cap)} total`;

function QuotaCard({ quota }: { quota: QuotaStatus }) {
  // Billed tokens with no resolvable rate, so the $ figure is a floor and the token ceiling is the
  // one binding. Same condition and same words as the run strip's pill (`shell/RemoteControl.tsx`).
  const blind = quota.spend_unpriced_tokens > 0;
  return (
    <ul className="quota-grid">
      <QuotaCell
        label="Spend to date"
        value={`${blind ? "≥ " : ""}${fmtUsd(quota.spend_used_total_usd)}`}
        sub={blind ? "⚠ USD cap inactive" : ofTotal(quota.spend_budget_usd_total, fmtUsd)}
        warn={
          blind
            ? `${fmtTokens(quota.spend_unpriced_tokens)} billed with no resolvable rate, so this figure undercounts. The token ceiling is the one holding.`
            : undefined
        }
      />
      <QuotaCell
        label="Tokens to date"
        value={fmtTokens(quota.tokens_used_total)}
        sub={ofTotal(quota.token_budget_total, fmtTokens)}
      />
      <QuotaCell
        label="Concurrent cycles"
        value={`${quota.concurrent_running} / ${quota.max_concurrent_cycles}`}
        sub="running now"
      />
      <QuotaCell
        label="Campaigns today"
        value={`${quota.campaigns_today} / ${quota.max_campaigns_per_day}`}
        sub="since UTC midnight"
      />
    </ul>
  );
}
