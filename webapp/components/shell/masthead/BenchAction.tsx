"use client";

import { useDashboardAt } from "@/lib/poll";
import { postCommand } from "@/lib/api/commands";
import { useCommand } from "@/lib/hooks/useCommand";
import { encodeCyclePath, pathRoot, type CyclePath } from "@/lib/ids";

export function BenchAction({ path }: { path: CyclePath }) {
  const dash = useDashboardAt(path);
  const cmd = useCommand<"grade-bench">("grade-bench", { scope: encodeCyclePath(path) });
  const status = dash?.bench_score?.status;
  if (!status?.offered) {
    return null;
  }
  const campaignId = pathRoot(path).campaignId;
  const busy = cmd.pending !== null || dash?.bench_pass != null;

  return (
    <>
      <button
        type="button"
        className="chip chip-btn"
        disabled={busy || !status.can_grade}
        onClick={() =>
          void cmd.run("grade-bench", () => postCommand("grade-bench", { campaign_id: campaignId }))
        }
        title={status.refusal ?? "Grade the origin and the selection on the held-out rows."}
      >
        {busy ? "Grading…" : "Grade bench"}
      </button>
      {cmd.failure ? <span className="chip chip-warn">{cmd.failure.message}</span> : null}
    </>
  );
}
