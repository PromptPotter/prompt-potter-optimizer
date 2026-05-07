"use client";
import { TERMS } from "@/lib/terms";
import type { StatusKind } from "@/lib/poll";

interface Props {
  kind: StatusKind;
  text: string;
  hint?: string;
  termKey?: string;
}

export function StatusBar({ kind, text, hint, termKey }: Props) {
  const tip = termKey ? TERMS[termKey] : "";
  return (
    <div
      className={`status-bar ${kind}`}
      role="status"
      aria-live="polite"
      aria-atomic="true"
      title={tip || undefined}
    >
      <span className="status-dot" aria-hidden="true" />
      <span>
        <strong>{text}</strong>
        {hint ? <span style={{ marginLeft: 8, opacity: 0.85 }}>{hint}</span> : null}
      </span>
    </div>
  );
}
