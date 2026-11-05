import { Empty, FailureLine, Loading } from "@/components/ui";
import { useCells } from "@/lib/hooks/useCells";
import { failureOf, shownData } from "@/lib/hooks/useRead";

// Null once the shared roster holds samples; every surface over it says its other states here.
export function RosterNote() {
  const { datasetName, read } = useCells({});
  const failure = failureOf(read);
  if (failure !== null) {
    return (
      <FailureLine
        failure={failure}
        lead={`Couldn’t read this campaign’s samples${datasetName ? ` (${datasetName})` : ""}.`}
      />
    );
  }
  if ((shownData(read)?.samples.length ?? 0) > 0) return null;
  return read.status === "ready" ? (
    <Empty>No samples on this campaign’s dataset yet.</Empty>
  ) : (
    <Loading>Loading this campaign’s samples…</Loading>
  );
}
