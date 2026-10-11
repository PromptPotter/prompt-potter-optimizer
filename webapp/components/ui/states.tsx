// Styled inline so they render even if component CSS hasn't loaded.

import type { CSSProperties, ReactNode } from "react";

const base: CSSProperties = { padding: 8, fontSize: "var(--text-base)" };

export function Empty({ children }: { children: ReactNode }) {
  return <div style={{ ...base, color: "var(--color-text-secondary)" }}>{children}</div>;
}

export function Loading({ children = "Loading…" }: { children?: ReactNode }) {
  return <div style={{ ...base, color: "var(--color-text-secondary)" }}>{children}</div>;
}

export function ErrorNote({ children }: { children: ReactNode }) {
  return (
    <div role="alert" style={{ ...base, color: "var(--color-danger)" }}>
      {children}
    </div>
  );
}

// The `/login/` trailing slash matches `next.config.ts::trailingSlash`.
export function SignInPrompt({
  message,
  className,
}: {
  message: ReactNode;
  className?: string;
}) {
  return (
    <div className={className} style={{ ...base, color: "var(--color-text-secondary)" }}>
      {message}{" "}
      <a href="/login/" style={{ color: "var(--color-accent)", fontWeight: 600 }}>
        Sign in
      </a>
    </div>
  );
}
