// Styled inline so they render even if component CSS hasn't loaded.

import type { CSSProperties, ReactNode } from "react";

const base: CSSProperties = { padding: 8, fontSize: "var(--text-base)" };

export function Empty({ children }: { children: ReactNode }) {
  return <div style={{ ...base, color: "var(--color-text-tertiary)" }}>{children}</div>;
}

export function Loading({ children = "Loading…" }: { children?: ReactNode }) {
  return <div style={{ ...base, color: "var(--color-text-tertiary)" }}>{children}</div>;
}

export function ErrorNote({ children }: { children: ReactNode }) {
  return (
    <div role="alert" style={{ ...base, color: "var(--color-danger)" }}>
      {children}
    </div>
  );
}

// What an auth-gated panel shows instead of firing a read that would 401
// (frontend-surface-contract.md § I1). Trailing slash matches `next.config.ts::trailingSlash`.
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
