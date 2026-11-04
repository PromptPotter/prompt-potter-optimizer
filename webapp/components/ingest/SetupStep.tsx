import { useState, type ReactNode } from "react";
import { SETUP_STEP_TITLES, type SetupStep as Step } from "@/lib/api/types.generated";

// Closed it is one line naming its current answer, so a phone sees every step and Start.
// It opens on a served gap naming it, and on nothing else.
export function SetupStep({
  step,
  answer,
  gapped,
  children,
}: {
  step: Step;
  answer: ReactNode;
  gapped: ReadonlySet<Step | null>;
  children: ReactNode;
}) {
  // Read once: React rewrites `open` on every prop change, which would shut a step the operator opened.
  const [initiallyOpen] = useState(gapped.has(step));
  return (
    <details className="setup-step" open={initiallyOpen}>
      <summary>
        <b>{SETUP_STEP_TITLES[step]}</b> <small>{answer}</small>
      </summary>
      {children}
    </details>
  );
}
