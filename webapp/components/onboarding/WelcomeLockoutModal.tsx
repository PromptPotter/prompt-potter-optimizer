"use client";
// Auth prompt — single tall modal opened by both the "Log in" and the
// "Sign up for free" chips. One surface, one set of copy: no mode
// branching, both chips drop the visitor at the same canonical entry.
//
// Reuses .account-overlay + .account-modal + .account-pane-head +
// .account-pane-body + .account-close + .login-button + .chat-input
// from globals.css. New: .auth-divider (OR rule), .auth-link (blue
// hyperlinks), .auth-legal-row (centered footer trio).

import { useEffect, useState } from "react";
import { instance } from "@/lib/instance";

interface Props {
  open: boolean;
  onClose: () => void;
  // OIDC callback bounce-back: the FastAPI /auth/callback/{provider} route
  // 303-redirects to /ui/?auth_error=<code>(&email=<addr>) on every failure
  // path. Topbar parses those params and passes them in here; we render a
  // one-line .account-error banner above the Continue-with-Google button.
  errorCode?: string | null;
  errorEmail?: string | null;
}

const LINKEDIN_URL = instance.owner.linkedin;
const TERMS_URL = `${instance.marketing_url}/terms`;
const PRIVACY_URL = `${instance.marketing_url}/privacy`;
const IMPRINT_URL = `${instance.marketing_url}/imprint`;

const AUTH_ERROR_COPY: Record<string, (email: string | null) => string> = {
  not_allowlisted: (email) =>
    email
      ? `${email} isn't on the beta list yet. Use the email/LinkedIn button below to request an invite.`
      : "That Google account isn't on the beta list yet. Use the email/LinkedIn button below to request an invite.",
  state_invalid_or_expired: () =>
    "That sign-in took too long and expired. Try Continue with Google again.",
  code_exchange_failed: () =>
    "We couldn't verify your sign-in with Google. Try again.",
  provider_returned_error: () =>
    "Google sign-in was cancelled. Try again when you're ready.",
  callback_missing_params: () =>
    "That sign-in link was incomplete. Try again.",
  signin_unavailable: () =>
    "Sign-in is temporarily unavailable. Ping us on LinkedIn and we'll fix it.",
  _default: () => "Something went wrong during sign-in. Try again.",
};

function authErrorMessage(code: string, email: string | null): string {
  const fn = AUTH_ERROR_COPY[code] ?? AUTH_ERROR_COPY._default;
  return fn(email);
}

export function WelcomeLockoutModal({ open, onClose, errorCode, errorEmail }: Props) {
  const [email, setEmail] = useState("");

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

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
      <div className="account-modal account-modal-auth">
        <header className="account-pane-head">
          <h3 id="auth-prompt-title">Log in or sign up</h3>
          <button
            type="button"
            className="account-close"
            aria-label="Close"
            onClick={onClose}
          >
            ×
          </button>
        </header>

        <div className="account-pane-body account-pane-body-auth">
          <p
            style={{
              margin: 0,
              fontSize: 20,
              lineHeight: 1.3,
              fontWeight: 500,
              color: "var(--color-text-primary)",
            }}
          >
            Sign up to evolve your prompts with real data &mdash; not guesses.
          </p>

          {errorCode ? (
            <p className="account-error" role="alert" style={{ margin: 0 }}>
              {authErrorMessage(errorCode, errorEmail ?? null)}
            </p>
          ) : null}

          <p
            style={{
              margin: 0,
              color: "var(--color-text-secondary)",
              lineHeight: 1.55,
            }}
          >
            By continuing, you agree to our{" "}
            <a className="auth-link" href={PRIVACY_URL}>
              Privacy Policy
            </a>
            .
          </p>

          <p style={{ margin: 0, color: "var(--color-text-secondary)", lineHeight: 1.55 }}>
            PromptPotter is invite-only while we&rsquo;re in beta. Sign in with the
            Google account that received your invite to access your campaigns.
          </p>

          <div className="login-buttons" style={{ marginTop: 4 }}>
            <a className="login-button" href="/api/v1/auth/login/google">
              Continue with Google
            </a>
          </div>

          <div className="auth-divider" aria-hidden="true">
            <span>OR</span>
          </div>

          <label
            htmlFor="auth-email"
            style={{
              fontSize: 11,
              textTransform: "uppercase",
              letterSpacing: "0.06em",
              color: "var(--color-text-tertiary)",
              marginBottom: -10,
            }}
          >
            Email address
          </label>
          <input
            id="auth-email"
            type="email"
            className="chat-input auth-email-input"
            placeholder="you@example.com"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
          />
          <a
            className="login-button"
            href={LINKEDIN_URL}
            target="_blank"
            rel="noopener noreferrer"
            style={{ textAlign: "center" }}
          >
            Continue
          </a>
          <p
            style={{
              margin: "-4px 0 0",
              fontSize: 11,
              color: "var(--color-text-tertiary)",
              lineHeight: 1.5,
            }}
          >
            Email sign-in isn&rsquo;t wired yet &mdash; Continue opens a quick
            chat on LinkedIn about beta access.
          </p>

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
