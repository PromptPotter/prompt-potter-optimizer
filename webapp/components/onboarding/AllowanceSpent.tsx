"use client";
// The end of the free allowance — a NOTICE, never a gate: what the spend bought stays readable.
// Otherwise the user sees only `quota.py`'s refusal, which is worded for the box's operator.

import { useState } from "react";
import { useAuth } from "@/lib/auth-context";
import { Dialog } from "@/components/ui";
import { fetchQuotaStatus } from "@/lib/api";
import { fmtUsd } from "@/lib/format";
import { readyData, useRead } from "@/lib/hooks/useRead";

const DISMISSED = "pp.allowance-spent.dismissed";

export function AllowanceSpent() {
  const { status, me } = useAuth();
  const quota = readyData(
    useRead({ key: "quota", fetch: fetchQuotaStatus }, { surface: "allowance", auth: true }),
  );
  const [dismissed, setDismissed] = useState(() => {
    try {
      return window.localStorage.getItem(DISMISSED) === "1";
    } catch {
      return false;
    }
  });

  // No ceiling = the box's operator; a null USD cap is metered by its token arm instead.
  const cap = quota?.spend_budget_usd_total ?? null;
  const spent = quota != null && cap !== null && quota.spend_used_total_usd >= cap;
  const open = status === "authed" && !!me && spent && !dismissed;

  if (!open || quota == null || cap === null) return null;

  const onDismiss = () => {
    try {
      window.localStorage.setItem(DISMISSED, "1");
    } catch {
      // Refused storage just shows this again next visit — the harmless direction.
    }
    setDismissed(true);
  };

  return (
    <Dialog open onClose={onDismiss} labelledBy="allowance-spent-title" bare>
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
    </Dialog>
  );
}
