import { Button, FailureLine } from "@/components/ui";
import { postCommand } from "@/lib/api/commands";
import { useCommand } from "@/lib/hooks/useCommand";

// A paid pass's stop, as every door sends it: `cancel-command` on the id its press was sent under.
// What it measured stays banked, and a rerun replays it.
export function StopCommand({ commandId }: { commandId: string }) {
  const cmd = useCommand<"cancel-command">("cancel-command", { scope: commandId });
  return (
    <span className="stop-command">
      <Button
        disabled={cmd.pending !== null}
        onClick={() =>
          void cmd.run("cancel-command", () =>
            postCommand("cancel-command", { command_id: commandId }),
          )
        }
      >
        {cmd.pending !== null ? "Stopping…" : "Stop"}
      </Button>
      <FailureLine failure={cmd.failure} />
    </span>
  );
}
