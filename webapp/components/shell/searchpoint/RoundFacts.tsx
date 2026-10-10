"use client";
import type { RoundReading } from "@/lib/derivations";
import { HoverCard } from "@/components/ui";

export function RoundFacts({ reading }: { reading: RoundReading | null }) {
  if (!reading || reading.facts.length === 0) return null;
  return (
    <div
      className="subject-facts"
      aria-label={`Round ${reading.round}, in the optimizer's words`}
    >
      <span>round {reading.round}</span>
      {reading.facts.map((f) =>
        f.kind === "stat" ? (
          <span key={f.key}>
            {f.label} {f.text}
          </span>
        ) : (
          <HoverCard key={f.key} content={<p className="subject-fact-note">{f.text}</p>}>
            <span tabIndex={0}>{f.label}</span>
          </HoverCard>
        ),
      )}
    </div>
  );
}

export function RoundVerdict({ reading }: { reading: RoundReading | null }) {
  if (!reading || reading.improved !== false || !reading.verdictReason) return null;
  return (
    <span className="subject-diff-verdict" title={reading.verdictReason}>
      round {reading.round}: {reading.verdictReason}
    </span>
  );
}
