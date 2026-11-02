"use client";
import type { ArmReading } from "@/lib/api/types";
import { fmtUnit } from "@/lib/format";
import { HoverCard } from "@/components/ui";

// The shown searchpoint's own accuracy; the bench and the run's spend are the masthead's alone.
export function SubjectHeadline({ reading }: { reading: ArmReading | null }) {
  const accuracy = reading?.own?.accuracy?.value;
  if (reading == null || accuracy == null) return null;
  const { scored, expected, cut } = reading.panel;
  return (
    <div className="subject-headline">
      <HoverCard
        className="subject-lift-card"
        focusable
        content={
          <>
            <p className="subject-lift-note">This candidate on the rows it measured.</p>
            {cut ? (
              <p className="subject-lift-note">
                Measurement stopped at {scored} of the round&rsquo;s {expected} samples — a
                partial panel, not a verdict.
              </p>
            ) : null}
          </>
        }
      >
        <span className="subject-headline-acc">
          {fmtUnit("rate", accuracy)}
          {cut ? (
            <span className="subject-headline-cut">
              {" "}
              {scored}/{expected}
            </span>
          ) : null}
        </span>
      </HoverCard>
    </div>
  );
}
