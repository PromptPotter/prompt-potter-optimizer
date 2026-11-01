"use client";

import { BRAND } from "@/lib/brand";
import { useAuth } from "@/lib/state/auth";
import { Dialog } from "@/components/ui";
import { AuthCore } from "@/components/login/AuthCore";

// Mounted ONCE (app/page.tsx): every trigger calls `openAuthPrompt()`, never an `open` prop.
export function WelcomeLockoutModal() {
  const { authPrompt, closeAuthPrompt } = useAuth();
  const { open, refusal } = authPrompt;

  if (!open) return null;

  return (
    <Dialog open title="Log in or sign up" onClose={closeAuthPrompt}>
      <div className="auth-prompt">
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

        <AuthCore refusal={refusal} />

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
    </Dialog>
  );
}
