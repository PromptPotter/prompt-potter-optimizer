"use client";
// Overlay for a BLOCKED account. It reflects the dispatcher's capability gate, never enforces
// it, and takes precedence over ConsentGate: a blocked account cannot submit, so has nothing to consent to.

import { useAuth } from "@/lib/auth-context";
import { postLogout } from "@/lib/api/account";
import { useCommand } from "@/lib/hooks/useCommand";
import { Dialog } from "@/components/ui";

export function AccessGate() {
  const { status, me } = useAuth();
  const cmd = useCommand<"logout">("access-gate", { revalidate: false });

  const open = status === "authed" && !!me && me.access_state === "blocked";

  if (!open) return null;

  // Navigate either way — a failed logout must not strand them on a dead overlay.
  const onSignOut = () =>
    void cmd.run("logout", postLogout).then(() => window.location.assign("/login"));

  return (
    <Dialog open labelledBy="access-gate-title" bare>
      <div className="account-modal consent-modal">
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
              disabled={cmd.pending !== null}
              onClick={onSignOut}
            >
              {cmd.pending !== null ? "Signing out…" : "Sign out"}
            </button>
          </div>
        </div>
      </div>
    </Dialog>
  );
}
