"use client";
// The end of the free allowance, said to the person who reached it.
//
// It is a NOTICE, not a gate. The two gates beside it block because a blocked
// account and an unaccepted Terms both mean "you may not use this"; a spent
// allowance means "you already did", and everything that spend bought is still
// theirs to read. Blocking here would take the results away as the reward for
// finishing the runs.
//
// Without it a spent-out account sees only `quota.py`'s refusal leaking through
// the generic API-error path — written to the operator of the box ("raise the
// account ceiling"), which is the one person a free-tier user is not.
//
// Reads the quota the account already answers to; it adds no endpoint and
// recomputes nothing, per webapp/CLAUDE.md § Scoring authority.
//
// Reuses .account-overlay / .account-modal / .account-pane-head /
// .account-pane-body from the account domain stylesheet; .consent-actions from
// the auth one.

import { useState } from "react";
import { useAuth } from "@/lib/auth-context";
import { fetchQuotaStatus } from "@/lib/api";
import { fmtUsd } from "@/lib/format";
import { useFetch } from "@/lib/hooks/useFetch";

const DISMISSED = "pp.allowance-spent.dismissed";

export function AllowanceSpent() {
  const { status, me } = useAuth();
  const { data: quota } = useFetch(() => fetchQuotaStatus(), []);
  const [dismissed, setDismissed] = useState(() => {
    try {
      return window.localStorage.getItem(DISMISSED) === "1";
    } catch {
      return false;
    }
  });

  // An account with no ceiling is the operator of the box, and one still blind on
  // USD is metered by its token arm — neither has spent an allowance it was given.
  const cap = quota?.spend_budget_usd_total ?? null;
  const spent = quota != null && cap !== null && quota.spend_used_total_usd >= cap;
  const open = status === "authed" && !!me && spent && !dismissed;

  if (!open || quota == null || cap === null) return null;

  const onDismiss = () => {
    try {
      window.localStorage.setItem(DISMISSED, "1");
    } catch {
      // A browser that refuses storage just shows this again next visit, which is
      // the harmless direction.
    }
    setDismissed(true);
  };

  return (
    <div className="account-overlay" role="dialog" aria-labelledby="allowance-spent-title">
      <div className="account-modal consent-modal">
        <header className="account-pane-head">
          <h3 id="allowance-spent-title">That&rsquo;s the last of your free runs</h3>
        </header>

        <div className="account-pane-body">
          <p className="auth-note">
            You ran PromptPotter to the end of what I set aside for it &mdash;{" "}
            <strong>{fmtUsd(quota.spend_used_total_usd)}</strong> of real model spend, on my key.
            Nothing here is taken away: every campaign you ran, every round it scored and every
            prompt it wrote stays yours to read and export.
          </p>
          <p className="auth-note">
            To keep going you&rsquo;ll need your own provider key. That is being wired up now, and
            it is the next thing to land.
          </p>

          <div className="consent-actions">
            <button type="button" className="login-button" onClick={onDismiss}>
              Back to my results
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
