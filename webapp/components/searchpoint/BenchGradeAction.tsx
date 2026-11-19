import { useState } from "react";
import { Button, FailureLine, Term } from "@/components/ui";
import { commands, mintCommandId } from "@/lib/api/commands";
import { PASS_REFUSAL_INFO } from "@/lib/api/types";
import { useCommand } from "@/lib/hooks/useCommand";
import { useViewedCycle } from "@/lib/state/lineage";
import { useWorkspace } from "@/lib/state/workspace";

// The bench's one control, on the Leader it grades: a refusal is the served reason, read on its Term.
export function BenchGradeAction() {
  const { viewed } = useWorkspace();
  const status = useViewedCycle()?.facts.bench?.status ?? null;
  const scope = viewed?.address ?? null;
  const cmd = useCommand<"held-out">("held-out", scope);
  const stop = useCommand<"cancel">("cancel", scope);
  // A press is the subject's it was pressed on: another campaign paints no Stop of it.
  const [acting, setActing] = useState<{ at: string | null; id: string } | null>(null);
  const stopping = acting?.at === scope ? acting.id : null;
  if (!viewed || !status?.offered) return null;
  const grading = cmd.pending !== null || status.refusal === "in_flight";
  const campaignId = viewed.root.campaignId;
  function grade() {
    const commandId = mintCommandId();
    setActing({ at: scope, id: commandId });
    void cmd
      .run("held-out", () => commands["held-out"]({ campaign_id: campaignId }, commandId))
      .finally(() => setActing((a) => (a?.id === commandId ? null : a)));
  }
  const button = (
    <Button disabled={grading || !status.can_run} onClick={grade}>
      {grading ? "Grading…" : "Grade held-out"}
    </Button>
  );
  return (
    <span className="bench-grade">
      {status.refusal === null ? (
        button
      ) : (
        <Term content={PASS_REFUSAL_INFO[status.refusal]}>{button}</Term>
      )}
      {stopping && (
        <Button
          disabled={stop.pending !== null}
          onClick={() => void stop.run("cancel", () => commands.cancel({ command_id: stopping }))}
        >
          Stop
        </Button>
      )}
      <FailureLine failure={cmd.failure} />
      <FailureLine failure={stop.failure} />
    </span>
  );
}
