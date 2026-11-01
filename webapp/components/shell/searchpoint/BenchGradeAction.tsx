"use client";
import { Button, FailureLine, Term } from "@/components/ui";
import { postCommand } from "@/lib/api/commands";
import { BENCH_REFUSAL_INFO } from "@/lib/api/types.generated";
import { useCommand } from "@/lib/hooks/useCommand";
import { encodeCyclePath, pathRoot } from "@/lib/ids";
import { useViewedCourse } from "@/lib/lineage";
import { useWorkspace } from "@/lib/workspace";

// The bench's one control, on the Leader it grades: a refusal is the served reason, read on its Term.
export function BenchGradeAction() {
  const { viewedPath } = useWorkspace();
  const status = useViewedCourse()?.facts.bench?.status ?? null;
  const cmd = useCommand<"grade-bench">("grade-bench", {
    scope: viewedPath ? encodeCyclePath(viewedPath) : null,
  });
  if (!viewedPath || !status?.offered) return null;
  const grading = cmd.pending !== null || status.refusal === "in_flight";
  const campaignId = pathRoot(viewedPath).campaignId;
  const button = (
    <Button
      disabled={grading || !status.can_grade}
      onClick={() =>
        void cmd.run("grade-bench", () => postCommand("grade-bench", { campaign_id: campaignId }))
      }
    >
      {grading ? "Grading…" : "Grade on bench"}
    </Button>
  );
  return (
    <span className="bench-grade">
      {status.refusal === null ? (
        button
      ) : (
        <Term content={BENCH_REFUSAL_INFO[status.refusal]}>{button}</Term>
      )}
      <FailureLine failure={cmd.failure} />
    </span>
  );
}
