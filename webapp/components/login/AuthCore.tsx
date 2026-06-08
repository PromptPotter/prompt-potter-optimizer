"use client";
// Shared auth entry — Continue-with-Google, the no-Google-account fallback
// (open a GitHub issue to request beta access), and the invite-only
// explanation. The single source of the actual sign-in controls + copy,
// rendered identically by the standalone /login page and the
// WelcomeLockoutModal overlay. Each surface wraps it with its own framing: the
// page adds a wordmark, the modal adds header chrome + value headline + legal
// footer — the popup-specific bits that would crowd the page.

import { BRAND } from "@/lib/brand";

// Beta-access request channel (the repo issue tracker; NEXT_PUBLIC_SUPPORT_URL-
// overridable for whitelabel). Same seam as the sidebar Support link.
const ISSUE_URL = BRAND.supportUrl;
// Waitlist on the origin marketing site (promptpotter.com). Null when a
// whitelabel host clears NEXT_PUBLIC_MARKETING_URL → "invite-only" stays plain.
const WAITLIST_URL = BRAND.marketing.url ? `${BRAND.marketing.url}/product#waitlist` : null;

const AUTH_ERROR_COPY: Record<string, (email: string | null) => string> = {
  not_allowlisted: (email) =>
    email
      ? `${email} isn't on the beta list yet. Open a GitHub issue below to request an invite.`
      : "That Google account isn't on the beta list yet. Open a GitHub issue below to request an invite.",
  state_invalid_or_expired: () =>
    "That sign-in took too long and expired. Try Continue with Google again.",
  code_exchange_failed: () => "We couldn't verify your sign-in with Google. Try again.",
  provider_returned_error: () => "Google sign-in was cancelled. Try again when you're ready.",
  callback_missing_params: () => "That sign-in link was incomplete. Try again.",
  signin_unavailable: () =>
    "Sign-in is temporarily unavailable. Open a GitHub issue and we'll fix it.",
  _default: () => "Something went wrong during sign-in. Try again.",
};

function authErrorMessage(code: string, email: string | null): string {
  const fn = AUTH_ERROR_COPY[code] ?? AUTH_ERROR_COPY._default;
  return fn(email);
}

interface Props {
  // OIDC callback bounce-back: the FastAPI /auth/callback/{provider} route
  // 303-redirects with ?auth_error=<code>(&email=<addr>) on failure. The modal
  // forwards those; we render a one-line .account-error banner above the
  // Continue-with-Google button.
  errorCode?: string | null;
  errorEmail?: string | null;
}

export function AuthCore({ errorCode, errorEmail }: Props) {
  return (
    <div className="auth-core">
      {errorCode ? (
        <p className="account-error" role="alert">
          {authErrorMessage(errorCode, errorEmail ?? null)}
        </p>
      ) : null}

      <p className="auth-note">
        PromptPotter is{" "}
        {WAITLIST_URL ? (
          <a className="auth-link" href={WAITLIST_URL} target="_blank" rel="noopener noreferrer">
            invite-only
          </a>
        ) : (
          "invite-only"
        )}{" "}
        while we&rsquo;re in beta. Sign in with the Google account that received your invite to access
        your campaigns.
      </p>

      <div className="login-buttons">
        <a className="login-button" href="/api/v1/auth/login/google">
          Continue with Google
        </a>
      </div>

      <p className="auth-fineprint">
        Google is the only sign-in. We federate identity and never store passwords, so there&rsquo;s no
        email-and-password option. No Google account?{" "}
        <a className="auth-link" href={ISSUE_URL} target="_blank" rel="noopener noreferrer">
          Open a GitHub issue
        </a>{" "}
        to request beta access.
      </p>
    </div>
  );
}
