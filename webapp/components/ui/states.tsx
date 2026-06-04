// Shared placeholder states — the muted "nothing here yet" note and the red
// "something failed" note a panel shows before (or instead of) its data.
// Styled inline so they render correctly even if component CSS hasn't loaded;
// pass `className` when a call site needs an extra layout slot.

import type { CSSProperties, ReactNode } from "react";

const base: CSSProperties = { padding: 8, fontSize: 13 };

export function Empty({
  children,
  className,
}: {
  children: ReactNode;
  className?: string;
}) {
  return (
    <div className={className} style={{ ...base, color: "var(--color-text-tertiary)" }}>
      {children}
    </div>
  );
}

export function Loading({
  children = "Loading…",
  className,
}: {
  children?: ReactNode;
  className?: string;
}) {
  return (
    <div className={className} style={{ ...base, color: "var(--color-text-tertiary)" }}>
      {children}
    </div>
  );
}

export function ErrorNote({
  children,
  className,
}: {
  children: ReactNode;
  className?: string;
}) {
  return (
    <div className={className} role="alert" style={{ ...base, color: "var(--color-danger)" }}>
      {children}
    </div>
  );
}

// The anon resting state for an auth-gated surface — what a panel shows
// instead of firing a read that would 401 (frontend-surface-contract.md § I1).
// The CTA links to the full-page sign-in (`/login`, the same route logout
// redirects to); trailing slash matches `next.config.ts::trailingSlash`.
export function SignInPrompt({
  message,
  className,
}: {
  message: ReactNode;
  className?: string;
}) {
  return (
    <div className={className} style={{ ...base, color: "var(--color-text-tertiary)" }}>
      {message}{" "}
      <a href="/login/" style={{ color: "var(--color-accent)", fontWeight: 600 }}>
        Sign in
      </a>
    </div>
  );
}
