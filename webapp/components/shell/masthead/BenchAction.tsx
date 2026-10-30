"use client";

import { FailureLine } from "@/components/ui";
import { useDashboardAt } from "@/lib/poll";
import { postCommand } from "@/lib/api/commands";
import { BENCH_REFUSAL_INFO } from "@/lib/api/types.generated";
import { useCommand } from "@/lib/hooks/useCommand";
import { encodeCyclePath, pathRoot, type CyclePath } from "@/lib/ids";
import { useViewedCourse } from "@/lib/lineage";

export function BenchAction({ path }: { path: CyclePath }) {
  const dash = useDashboardAt(path);
  const cmd = useCommand<"grade-bench">("grade-bench", { scope: encodeCyclePath(path) });
  const status = useViewedCourse()?.facts.bench?.status;
  if (!status?.offered) {
    return null;
  }
  const campaignId = pathRoot(path).campaignId;
  const busy =
    cmd.pending !== null ||
    dash?.verification.some((m) => m.kind === "bench" && m.state === "running") === true;

  return (
    <>
      <button
        type="button"
        className="chip chip-btn"
        disabled={busy || !status.can_grade}
        onClick={() =>
          void cmd.run("grade-bench", () => postCommand("grade-bench", { campaign_id: campaignId }))
        }
        title={
          status.refusal === null
            ? "Grade the origin and the selection on the held-out rows."
            : BENCH_REFUSAL_INFO[status.refusal]
        }
      >
        {busy ? "Grading…" : "Grade bench"}
      </button>
      <FailureLine failure={cmd.failure} />
    </>
  );
}
