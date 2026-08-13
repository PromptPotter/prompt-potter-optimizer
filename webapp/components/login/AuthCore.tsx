"use client";
// Shared auth entry — Google sign-in (the ONLY method, with the Google mark on
// the button) and the Google-only / no-password explanation kept VISIBLE (an
// earlier "Why Google only?" popover that hid it tested worse — operator call).
// The single source of the sign-in controls + copy, rendered identically by the
// standalone /login page and the WelcomeLockoutModal overlay. Each surface wraps
// it with its own framing: the page adds a wordmark, the modal adds header
// chrome + value headline + legal footer.
//
// There is no "request access" affordance and no invite framing: signing in IS
// how you get an account. Whether that account may run anything is resolved
// after sign-in and shown by AccessGate, so this surface no longer has a
// rejection to explain.

// Google's "G" mark — inline so the one sign-in method carries its provider's
// logo (an unmistakable "use Google" signal). Decorative; the button text is
// the accessible label.
function GoogleMark() {
  return (
    <svg className="google-mark" viewBox="0 0 18 18" width="18" height="18" aria-hidden="true">
      <path
        fill="#4285F4"
        d="M17.64 9.2c0-.64-.06-1.25-.16-1.84H9v3.48h4.84a4.14 4.14 0 0 1-1.8 2.71v2.26h2.92a8.78 8.78 0 0 0 2.68-6.61z"
      />
      <path
        fill="#34A853"
        d="M9 18c2.43 0 4.47-.8 5.96-2.18l-2.92-2.26c-.81.54-1.84.86-3.04.86-2.34 0-4.32-1.58-5.03-3.71H.96v2.33A9 9 0 0 0 9 18z"
      />
      <path
        fill="#FBBC05"
        d="M3.97 10.71a5.41 5.41 0 0 1 0-3.42V4.96H.96a9 9 0 0 0 0 8.09l3.01-2.34z"
      />
      <path
        fill="#EA4335"
        d="M9 3.58c1.32 0 2.5.45 3.44 1.35l2.58-2.59A9 9 0 0 0 9 0 9 9 0 0 0 .96 4.96l3.01 2.34C4.68 5.16 6.66 3.58 9 3.58z"
      />
    </svg>
  );
}

const AUTH_ERROR_FALLBACK = (): string => "Something went wrong during sign-in. Try again.";

const AUTH_ERROR_COPY: Record<string, (email: string | null) => string> = {
  state_invalid_or_expired: () =>
    "That sign-in took too long and expired. Try Continue with Google again.",
  code_exchange_failed: () => "We couldn't verify your sign-in with Google. Try again.",
  provider_returned_error: () => "Google sign-in was cancelled. Try again when you're ready.",
  callback_missing_params: () => "That sign-in link was incomplete. Try again.",
  signin_unavailable: () =>
    "Sign-in is temporarily unavailable. Open a GitHub issue and we'll fix it.",
};

function authErrorMessage(code: string, email: string | null): string {
  const fn = AUTH_ERROR_COPY[code] ?? AUTH_ERROR_FALLBACK;
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
        Create your account with Google. PromptPotter is opening up in stages, so running
        campaigns switches on once we reach you &mdash; your account is saved either way.
      </p>

      <div className="login-buttons">
        <a className="login-button" href="/api/v1/auth/login/google">
          <GoogleMark />
          Continue with Google
        </a>
      </div>

      <p className="auth-fineprint">
        Google is the only sign-in. We federate identity and never store passwords, so there&rsquo;s no
        email-and-password option.
      </p>
    </div>
  );
}
