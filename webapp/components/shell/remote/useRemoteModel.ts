import { commands, cycleAddress } from "@/lib/api";
import { useCommand } from "@/lib/hooks/useCommand";
import { useViewedDashboard } from "@/lib/poll";
import { encodeCyclePath } from "@/lib/ids";
import { useEntry } from "@/lib/hooks/useRegistry";
import { useWorkspace } from "@/lib/workspace";

export type RemoteModel = NonNullable<ReturnType<typeof useRemoteModel>>;

export function useRemoteModel() {
  const { viewed, navigate } = useWorkspace();
  const viewedPath = viewed?.path ?? null;
  const leafEntry = useEntry(viewedPath ? encodeCyclePath(viewedPath) : null);
  const { dash, isLive } = useViewedDashboard();
  const cmd = useCommand<"skip" | "sample-lookahead">("remote-control");
  const isOuterView = (viewedPath?.length ?? 1) === 1;

  if (!viewedPath) return null;

  // No `settled` gate: a verb is still offered there, and this gates `RunControlButton`'s only mount.
  if (!dash) return null;
  const { lookahead } = dash;
  const innerHop = isLive ? dash.current_round.inner : null;
  const arm = (cells: number, auto: boolean) =>
    void cmd.run("sample-lookahead", () =>
      // `cells: 1` disarms; sent unclamped, the walk clamps it to `lookahead.ceiling`.
      commands["set-sample-lookahead"]({ ...cycleAddress(viewedPath), cells, auto }),
    );
  // A depth press keeps the every-round switch where it stands; only the switch moves it.
  const armCells = (raw: string) => {
    const cells = Number(raw);
    if (cells === lookahead.depth) return;
    arm(cells, lookahead.auto);
  };

  return {
    babysat: leafEntry?.level === "course" && leafEntry.state.intervened,
    inner: !isOuterView,
    innerHop,
    toOuter: () => navigate({ path: viewedPath.slice(0, 1) }),
    toInner: (campaignId: string, cycleId: string) =>
      navigate({ path: [...viewedPath, { campaignId, cycleId }] }),
    skipRefusal: dash.admission.skip_refusal,
    // RunLimitsControl reads the cycle's spend beside the caps it edits.
    metered: dash.spend_metered,
    budget: dash.run_limits.budget,
    overshoot: dash.run_limits.overshoot,
    share: dash.allotment,
    lookahead,
    heldCall: dash.held_call,
    providerHeld: dash.backpressure,
    arm,
    armCells,
    skip: () =>
      void cmd.run("skip", () => commands["skip-searchpoint"](cycleAddress(viewedPath))),
    pending: cmd.pending,
    failure: cmd.failure,
  };
}
