
import type { ReactNode } from "react";

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

export function AccountFailure({ message, subject }: { message: string; subject: string }) {
  return (
    <p className="account-failure" role="alert">
      Could not load {subject}. {message}
    </p>
  );
}

export function AccountLoading({ subject }: { subject: string }) {
  return <p className="account-loading">Reading {subject}…</p>;
}
