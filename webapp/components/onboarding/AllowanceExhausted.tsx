"use client";
// A NOTICE, never a gate: what the spend bought stays readable.

import { useState } from "react";
import { useAuth } from "@/lib/state/auth";
import { Dialog } from "@/components/ui";
import { couponRead } from "@/lib/api";
import { fmtUsd } from "@/lib/format";
import { STORAGE_KEYS } from "@/lib/hooks/useLocalStorage";
import { readyData, useRead } from "@/lib/hooks/useRead";
import { useWorkspace } from "@/lib/workspace";

export function AllowanceExhausted() {
  const { status, me } = useAuth();
  const { openAccount } = useWorkspace();
  const coupon = readyData(useRead(couponRead(), { auth: true }));
  const [dismissed, setDismissed] = useState(() => {
    try {
      return window.localStorage.getItem(STORAGE_KEYS.allowanceExhaustedDismissed) === "1";
    } catch {
      return false;
    }
  });

  // An account holding a key of its own still sends on it, so the coupon running out stops nothing it asked for.
  const open =
    status === "authed" &&
    !!me &&
    coupon?.exhausted === true &&
    coupon.keys.length === 0 &&
    !dismissed;

  if (!open) return null;

  const onDismiss = () => {
    try {
      window.localStorage.setItem(STORAGE_KEYS.allowanceExhaustedDismissed, "1");
    } catch {
      // Refused storage just shows this again next visit — the harmless direction.
    }
    setDismissed(true);
  };

  const onAddKey = () => {
    onDismiss();
    openAccount("keys");
  };

  return (
    <Dialog open onClose={onDismiss} labelledBy="allowance-exhausted-title" bare>
      <div className="account-modal consent-modal">
        <header className="account-pane-head">
          <h3 id="allowance-exhausted-title">That&rsquo;s the last of your free runs</h3>
        </header>

        <div className="account-pane-body">
          <p className="auth-note">
            You ran PromptPotter to the end of what I set aside for it &mdash;{" "}
            <strong>{fmtUsd(coupon.host_key_spend.billed_usd)}</strong> of real model spend, on my
            key. Nothing here is taken away: every campaign you ran, every round it scored and every
            prompt it wrote stays yours to read and export.
          </p>
          <p className="auth-note">
            To keep going, add a provider key of your own under Account &rarr; API keys. Your runs
            then send on it, billed to you by that provider.
          </p>

          <div className="consent-actions">
            <button type="button" className="login-button" onClick={onAddKey}>
              Add my own key
            </button>
            <button type="button" className="login-button" onClick={onDismiss}>
              Back to my results
            </button>
          </div>
        </div>
      </div>
    </Dialog>
  );
}
