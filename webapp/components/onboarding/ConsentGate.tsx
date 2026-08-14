"use client";
// Post-auth consent gate — the provable-consent surface.
//
// Mounts for a SIGNED-IN user whose accepted Terms version doesn't match the
// live one (`me.terms_accepted_version !== me.terms_version`), and blocks the
// app behind a non-dismissable overlay until they tick the box and accept. The
// accept POSTs `/auth/accept-terms`, which writes the provable record
// (version + server-stamped timestamp) to user.json, then re-probes `/auth/me`
// so the gate clears. An anon visitor (read-only public preview) is never
// gated — consent attaches only when someone is about to actually submit data.
// A PENDING account is never gated either, for that same reason: it holds no
// capability, so it is not about to submit anything. AccessGate has it instead.
//
// Unlike WelcomeLockoutModal this has no close affordance: no ×, no
// overlay-click dismiss, no ESC (the a11y hook's onClose is a no-op). The only
// way out is to agree — that's the point of a gate.
//
// Reuses .account-overlay / .account-modal / .account-pane-head /
// .account-pane-body from the account domain stylesheet; .consent-* live in
// the auth domain stylesheet.

import { useState } from "react";
import { BRAND } from "@/lib/brand";
import { useAuth } from "@/lib/auth-context";
import { acceptTerms } from "@/lib/api/account";
import { useDialogA11y } from "@/lib/hooks/useDialogA11y";

const NOOP = () => {};

export function ConsentGate() {
  const { status, me, refresh } = useAuth();
  const [checked, setChecked] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Focus-trap + focus-into-card, but NO ESC dismiss — onClose is a no-op so
  // the gate can't be keyboard-escaped.
  const open =
    status === "authed" &&
    !!me &&
    me.access_state === "active" &&
    me.terms_accepted_version !== me.terms_version;
  const cardRef = useDialogA11y(open, NOOP);

  if (!open) return null;

  const onAccept = async () => {
    setSubmitting(true);
    setError(null);
    try {
      await acceptTerms(me.terms_version);
      refresh(); // re-probe /auth/me → terms_accepted_version matches → gate clears
    } catch {
      // A 409 means the displayed terms went stale mid-session; re-probing pulls
      // the current version so the gate re-renders against it.
      setError("Couldn't record that — reloading the current terms. Try again.");
      setSubmitting(false);
      refresh();
    }
  };

  return (
    <div
      className="account-overlay"
      role="dialog"
      aria-modal="true"
      aria-labelledby="consent-gate-title"
    >
      <div ref={cardRef} className="account-modal consent-modal">
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

          {error ? (
            <p className="account-error" role="alert">
              {error}
            </p>
          ) : null}

          <div className="consent-actions">
            <button
              type="button"
              className="login-button"
              disabled={!checked || submitting}
              onClick={onAccept}
            >
              {submitting ? "Recording…" : "Agree & continue"}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
