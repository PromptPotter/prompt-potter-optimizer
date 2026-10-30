"use client";
import { Empty, FailureLine, Loading } from "@/components/ui";
import { useHardSamples } from "@/lib/hard-samples";

// Null once the shared roster holds samples; every surface over it says its other states here.
export function RosterNote() {
  const { datasetName, items, stale, failure } = useHardSamples();
  if (failure !== null) {
    return (
      <FailureLine
        failure={failure}
        lead={`Couldn’t read this campaign’s samples${datasetName ? ` (${datasetName})` : ""}.`}
      />
    );
  }
  if (items.length > 0) return null;
  return stale ? (
    <Loading>Loading this campaign’s samples…</Loading>
  ) : (
    <Empty>No samples on this campaign’s dataset yet.</Empty>
  );
}
