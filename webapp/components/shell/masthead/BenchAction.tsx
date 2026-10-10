"use client";

import { READING_STATE_KINDS } from "@/lib/api/types.generated";
import { useDashboardAt } from "@/lib/poll";
import { postGradeBench } from "@/lib/api/commands";
import { useCommand } from "@/lib/hooks/useCommand";
import { encodeCyclePath, type CyclePath } from "@/lib/ids";

export function BenchAction({ path }: { path: CyclePath }) {
  const dash = useDashboardAt(path);
  const cmd = useCommand<"grade-bench">("grade-bench", { scope: encodeCyclePath(path) });
  const status = dash?.bench_score?.status;
  if (!status || !(status.can_grade || READING_STATE_KINDS[status.state] === "waiting")) {
    return null;
  }
  const busy = cmd.pending !== null || dash?.bench_pass != null;

  return (
    <>
      <button
        type="button"
        className="chip chip-btn"
        disabled={busy || !status.can_grade}
        onClick={() => void cmd.run("grade-bench", () => postGradeBench(path))}
        title={status.refusal ?? "Grade the origin and the selection on the held-out rows."}
      >
        {busy ? "Grading…" : "Grade bench"}
      </button>
      {cmd.failure ? <span className="chip chip-warn">{cmd.failure.message}</span> : null}
    </>
  );
}
