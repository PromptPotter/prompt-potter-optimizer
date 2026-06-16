"use client";
// Auth prompt overlay — opened by both the "Log in" and the "Sign up for free"
// chips. One surface, one set of copy; both chips drop the visitor at the same
// canonical entry.
//
// This is the *popup-specific layer* around the shared <AuthCore/>: the modal
// chrome (overlay + focus-trap + header/×), the value headline, the Privacy
// microcopy, and the legal footer trio — the bits that would crowd the
// standalone /login page, which renders the bare core instead.
//
// Reuses .account-overlay + .account-modal + .account-pane-head +
// .account-pane-body + .account-close, plus .auth-headline / .auth-note /
// .auth-link / .auth-legal-row from the auth domain stylesheet.

import { instance } from "@/lib/brand";
import { useDialogA11y } from "@/lib/hooks/useDialogA11y";
import { AuthCore } from "@/components/login/AuthCore";

interface Props {
  open: boolean;
  onClose: () => void;
  errorCode?: string | null;
  errorEmail?: string | null;
}

const TERMS_URL = `${instance.marketing_url}/terms`;
const PRIVACY_URL = `${instance.marketing_url}/privacy`;
const IMPRINT_URL = `${instance.marketing_url}/imprint`;

export function WelcomeLockoutModal({ open, onClose, errorCode, errorEmail }: Props) {
  // ESC + focus-trap + focus-restore from the shared hook; this modal keeps its
  // own tall .account-modal-auth layout rather than Dialog's confirm-card.
  const cardRef = useDialogA11y(open, onClose);

  if (!open) return null;

  return (
    <div
      className="account-overlay"
      role="dialog"
      aria-modal="true"
      aria-labelledby="auth-prompt-title"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div ref={cardRef} className="account-modal account-modal-auth">
        <header className="account-pane-head">
          <h3 id="auth-prompt-title">Log in or sign up</h3>
          <button type="button" className="account-close" aria-label="Close" onClick={onClose}>
            ×
          </button>
        </header>

        <div className="account-pane-body account-pane-body-auth">
          <p className="auth-headline">
            Sign up to evolve your prompts with real data &mdash; not guesses.
          </p>
          <p className="auth-note">
            By continuing, you agree to our{" "}
            <a className="auth-link" href={PRIVACY_URL}>
              Privacy Policy
            </a>
            .
          </p>

          <AuthCore errorCode={errorCode} errorEmail={errorEmail} />

          <div style={{ flex: 1 }} />

          <nav className="auth-legal-row" aria-label="Legal">
            <a className="auth-link" href={TERMS_URL}>
              Terms
            </a>
            <span aria-hidden="true">&middot;</span>
            <a className="auth-link" href={PRIVACY_URL}>
              Privacy
            </a>
            <span aria-hidden="true">&middot;</span>
            <a className="auth-link" href={IMPRINT_URL}>
              Imprint
            </a>
          </nav>
        </div>
      </div>
    </div>
  );
}
