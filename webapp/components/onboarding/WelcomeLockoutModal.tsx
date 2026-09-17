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
// Reuses .account-modal + .account-pane-head + .account-pane-body, plus
// .auth-headline / .auth-note / .auth-link / .auth-legal-row from the auth
// domain stylesheet.

import { BRAND } from "@/lib/brand";
import { useAuth } from "@/lib/auth-context";
import { Button, Dialog, IconClose } from "@/components/ui";
import { AuthCore } from "@/components/login/AuthCore";

// Props-free on purpose: it is mounted ONCE (app/page.tsx) and every trigger —
// the sidebar footer chips, the mobile app bar chips, the OIDC `?auth_error=`
// bounce-back — opens it through `openAuthPrompt()`. Threading `open` down
// instead would put one modal per chip mount point on screen.
export function WelcomeLockoutModal() {
  const { authPrompt, closeAuthPrompt } = useAuth();
  const { open, code: errorCode, email: errorEmail } = authPrompt;

  if (!open) return null;

  return (
    <Dialog open onClose={closeAuthPrompt} labelledBy="auth-prompt-title" bare>
      <div className="account-modal account-modal-auth">
        <header className="account-pane-head">
          <h3 id="auth-prompt-title">Log in or sign up</h3>
          <Button variant="ghost" aria-label="Close" onClick={closeAuthPrompt}>
            <IconClose />
          </Button>
        </header>

        <div className="account-pane-body account-pane-body-auth">
          <p className="auth-headline">
            Sign up to evolve your prompts with real data &mdash; not guesses.
          </p>
          <p className="auth-note">
            By continuing, you agree to our{" "}
            <a className="auth-link" href={BRAND.legal.privacy}>
              Privacy Policy
            </a>
            .
          </p>

          <AuthCore errorCode={errorCode} errorEmail={errorEmail} />

          <div style={{ flex: 1 }} />

          <nav className="auth-legal-row" aria-label="Legal">
            <a className="auth-link" href={BRAND.legal.terms}>
              Terms
            </a>
            <span aria-hidden="true">&middot;</span>
            <a className="auth-link" href={BRAND.legal.privacy}>
              Privacy
            </a>
            <span aria-hidden="true">&middot;</span>
            <a className="auth-link" href={BRAND.legal.imprint}>
              Imprint
            </a>
          </nav>
        </div>
      </div>
    </Dialog>
  );
}
