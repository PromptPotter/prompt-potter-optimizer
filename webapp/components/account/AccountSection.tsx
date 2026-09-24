
import type { ReactNode } from "react";
import type { FailureKind } from "@/lib/api";

export function AccountSection({
  title,
  lede,
  aside,
  children,
}: {
  title: string;
  lede?: ReactNode;
  aside?: ReactNode;
  children?: ReactNode;
}) {
  return (
    <section className="account-section">
      <header className="account-section-head">
        <div className="account-section-titles">
          <h4>{title}</h4>
          {lede ? <p>{lede}</p> : null}
        </div>
        {aside}
      </header>
      {children}
    </section>
  );
}

export function AccountEmpty({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="account-empty">
      <p className="account-empty-title">{title}</p>
      <p className="account-empty-body">{children}</p>
    </div>
  );
}

// Never a transport string (`frontend-surface-contract.md::I2`).
const FAILURE_SENTENCE: Record<FailureKind, string> = {
  transient: "The server did not answer. It retries when you reopen this pane.",
  auth: "Your session has ended. Sign in again to see this.",
  gone: "The server no longer has this.",
  denied: "This account may not read this.",
  invalid: "The server refused the request.",
};

export function AccountFailure({ kind, subject }: { kind: FailureKind | null; subject: string }) {
  return (
    <p className="account-failure" role="alert">
      Could not load {subject}. {FAILURE_SENTENCE[kind ?? "transient"]}
    </p>
  );
}

export function AccountLoading({ subject }: { subject: string }) {
  return <p className="account-loading">Reading {subject}…</p>;
}
