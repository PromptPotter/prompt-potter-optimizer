"use client";
// The auth prompt: popup chrome around the shared <AuthCore/>, which /login renders bare.

import { BRAND } from "@/lib/brand";
import { useAuth } from "@/lib/auth-context";
import { Button, Dialog, IconClose } from "@/components/ui";
import { AuthCore } from "@/components/login/AuthCore";

// Props-free: mounted ONCE (app/page.tsx); every trigger calls `openAuthPrompt()`, never an `open` prop.
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
