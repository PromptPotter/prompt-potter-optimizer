"use client";
// Post-auth entitlement gate — the state between "has an account" and "may use
// it".
//
// Signing up IS the grant now, so this is no longer a waiting room: it mounts
// only for an account the operator has BLOCKED (`me.access_state === "blocked"`)
// and holds the app behind a non-dismissable overlay. Nothing here submits: the
// server already refuses a blocked account's commands at the dispatcher's
// capability gate, so this reflects that state rather than enforcing it
// (webapp/CLAUDE.md § Scoring authority, same posture).
//
// It takes precedence over ConsentGate. Consent attaches when someone is about
// to submit data, and a blocked account cannot — asking them to accept Terms
// first would collect a consent for something they can't do.
//
// Unlike ConsentGate there IS a way out, because there is nothing to agree to:
// signing out is the only action a blocked user can take, so it is the one
// affordance. Still no × / ESC / overlay-click — leaving means leaving.
//
// Reuses .account-overlay / .account-modal / .account-pane-head /
// .account-pane-body from the account domain stylesheet; .consent-actions from
// the auth one.

import { useState } from "react";
import { useAuth } from "@/lib/auth-context";
import { postLogout } from "@/lib/api/account";
import { useDialogA11y } from "@/lib/hooks/useDialogA11y";

const NOOP = () => {};

export function AccessGate() {
  const { status, me } = useAuth();
  const [leaving, setLeaving] = useState(false);

  const open = status === "authed" && !!me && me.access_state === "blocked";
  const cardRef = useDialogA11y(open, NOOP);

  if (!open) return null;

  const onSignOut = async () => {
    setLeaving(true);
    try {
      await postLogout();
    } finally {
      // Hard navigation either way — a failed logout still shouldn't strand
      // them on a blocking overlay with a dead button.
      window.location.assign("/login");
    }
  };

  return (
    <div
      className="account-overlay"
      role="dialog"
      aria-modal="true"
      aria-labelledby="access-gate-title"
    >
      <div ref={cardRef} className="account-modal consent-modal">
        <header className="account-pane-head">
          <h3 id="access-gate-title">This account is switched off</h3>
        </header>

        <div className="account-pane-body">
          <p className="auth-note">
            {me.email ? <strong>{me.email}</strong> : "This account"} is signed in, but access to
            PromptPotter has been withdrawn for it. Nothing here will change that &mdash; whoever
            runs this instance has to switch it back on.
          </p>

          <div className="consent-actions">
            <button
              type="button"
              className="login-button"
              disabled={leaving}
              onClick={onSignOut}
            >
              {leaving ? "Signing out…" : "Sign out"}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
