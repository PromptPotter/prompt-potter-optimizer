
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
