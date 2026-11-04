import { Said } from "@/components/ui";
import { SIGN_IN_REFUSAL_INFO } from "@/lib/api/types.generated";
import type { SignInRefusal } from "@/lib/state/auth";

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

export function AuthCore({ refusal = null }: { refusal?: SignInRefusal | null }) {
  return (
    <div className="auth-core">
      {refusal ? <Said {...SIGN_IN_REFUSAL_INFO[refusal]} /> : null}

      <p className="auth-note">
        Create your account with Google. Signing up is the grant: 10 optimization runs on your
        own data, up to 10 rounds each, on my key.
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
