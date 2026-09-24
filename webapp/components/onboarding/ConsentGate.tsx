"use client";
// Provable-consent gate: `/auth/accept-terms` writes the record server-side. Consent attaches only
// to someone about to submit data, so anon visitors and non-active accounts are never gated.

import { useState } from "react";
import { BRAND } from "@/lib/brand";
import { useAuth } from "@/lib/auth-context";
import { acceptTerms } from "@/lib/api/account";
import { useCommand } from "@/lib/hooks/useCommand";
import { Dialog } from "@/components/ui";

export function ConsentGate() {
  const { status, me, refresh } = useAuth();
  const [checked, setChecked] = useState(false);
  // One sentence for every refusal: the re-probe below reloads the live terms either way.
  const cmd = useCommand<"accept-terms">("consent-gate", {
    revalidate: false,
    describe: () => "Couldn't record that — reloading the current terms. Try again.",
  });

  const open =
    status === "authed" &&
    !!me &&
    me.access_state === "active" &&
    me.terms_accepted_version !== me.terms_version;

  if (!open) return null;

  // A 409 means the displayed terms went stale mid-session; the re-probe pulls the current ones.
  const onAccept = () =>
    void cmd.run("accept-terms", () => acceptTerms(me.terms_version), refresh).then((r) => {
      if (!r.ok) refresh();
    });

  return (
    <Dialog open labelledBy="consent-gate-title" bare>
      <div className="account-modal consent-modal">
        <header className="account-pane-head">
          <h3 id="consent-gate-title">One thing before you start</h3>
        </header>

        <div className="account-pane-body">
          <p className="auth-note">
            PromptPotter keeps the prompts you submit and uses them to improve the tool.
            That&rsquo;s the trade for using it free &mdash; so it only works for
            non-sensitive material. Don&rsquo;t paste passwords, personal data, or anything
            confidential or owned by someone else.
          </p>

          <label className="consent-check">
            <input
              type="checkbox"
              checked={checked}
              onChange={(e) => setChecked(e.target.checked)}
            />
            <span>
              I agree to the{" "}
              <a
                className="auth-link"
                href={BRAND.legal.terms}
                target="_blank"
                rel="noopener noreferrer"
              >
                Terms
              </a>{" "}
              and{" "}
              <a
                className="auth-link"
                href={BRAND.legal.privacy}
                target="_blank"
                rel="noopener noreferrer"
              >
                Privacy Policy
              </a>
              , I won&rsquo;t submit sensitive, confidential, or third-party data, and I consent
              to my non-sensitive submissions being used to improve PromptPotter.
            </span>
          </label>

          {cmd.failure ? (
            <p className="account-error" role="alert">
              {cmd.failure.message}
            </p>
          ) : null}

          <div className="consent-actions">
            <button
              type="button"
              className="login-button"
              disabled={!checked || cmd.pending !== null}
              onClick={onAccept}
            >
              {cmd.pending !== null ? "Recording…" : "Agree & continue"}
            </button>
          </div>
        </div>
      </div>
    </Dialog>
  );
}
