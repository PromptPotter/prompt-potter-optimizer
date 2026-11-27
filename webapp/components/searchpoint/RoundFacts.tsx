import type { ServedRound } from "@/lib/api/types";
import { HoverCard } from "@/components/ui";

export function RoundFacts({ reading }: { reading: ServedRound | null }) {
  if (!reading || reading.optimizer_facts.length === 0) return null;
  return (
    <div
      className="subject-facts"
      aria-label={`Round ${reading.round}, in the optimizer's words`}
    >
      <span>round {reading.round}</span>
      {reading.optimizer_facts.map((f) =>
        f.kind === "stat" ? (
          <span key={f.key}>
            {f.label} {f.text}
          </span>
        ) : (
          <HoverCard key={f.key} content={<p className="subject-fact-note">{f.text}</p>} focusable>
            {f.label}
          </HoverCard>
        ),
      )}
    </div>
  );
}

export function RoundVerdict({ reading }: { reading: ServedRound | null }) {
  if (!reading || reading.ending === null || reading.ending === "crowned" || !reading.verdict_reason)
    return null;
  return (
    <span className="subject-diff-verdict" title={reading.verdict_reason}>
      round {reading.round}: {reading.verdict_reason}
    </span>
  );
}
