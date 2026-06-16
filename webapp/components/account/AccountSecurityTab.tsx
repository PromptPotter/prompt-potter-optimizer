"use client";
// Security pane — sign-in provider badge + quota status + sign-out.

import { useState } from "react";
import { PROVIDER_LABEL } from "./providers";
import { fmtUsd } from "@/lib/format";
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

function QuotaCard({ quota }: { quota: QuotaStatus }) {
  const spendCap = quota.spend_budget_usd_daily;
  return (
    <ul className="quota-grid">
      <li className="quota-cell">
        <span className="quota-cell-label">Spend today</span>
        <span className="quota-cell-value">{fmtUsd(quota.spend_used_today_usd)}</span>
        <span className="quota-cell-sub">
          {spendCap !== null ? `of ${fmtUsd(spendCap)} daily cap` : "no daily cap"}
        </span>
      </li>
      <li className="quota-cell">
        <span className="quota-cell-label">Concurrent cycles</span>
        <span className="quota-cell-value">
          {quota.concurrent_running} / {quota.max_concurrent_cycles}
        </span>
        <span className="quota-cell-sub">running now</span>
      </li>
      <li className="quota-cell">
        <span className="quota-cell-label">Campaigns today</span>
        <span className="quota-cell-value">
          {quota.campaigns_today} / {quota.max_campaigns_per_day}
        </span>
        <span className="quota-cell-sub">since UTC midnight</span>
      </li>
    </ul>
  );
}
