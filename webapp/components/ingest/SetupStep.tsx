"use client";

import { useState, type ReactNode } from "react";

// Closed it is one line naming its current answer, so a phone sees every step and Start.
export function SetupStep({
  title,
  answer,
  open,
  children,
}: {
  title: string;
  answer: ReactNode;
  open: boolean;
  children: ReactNode;
}) {
  // Read once: React rewrites `open` on every prop change, which would shut a step the operator opened.
  const [initiallyOpen] = useState(open);
  return (
    <details className="setup-step" open={initiallyOpen}>
      <summary>
        <b>{title}</b> <small>{answer}</small>
      </summary>
      {children}
    </details>
  );
}
