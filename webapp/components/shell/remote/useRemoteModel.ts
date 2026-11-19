import { useState } from "react";
import { commands, cycleAddress } from "@/lib/api";
import type { LookaheadAbreastStep, LookaheadDepth } from "@/lib/api/types";
import { useCommand } from "@/lib/hooks/useCommand";
import { useViewedDashboard } from "@/lib/state/poll";
import { encodeCyclePath } from "@/lib/ids";
import { useEntry } from "@/lib/hooks/useRegistry";
import { useWorkspace } from "@/lib/state/workspace";

export type RemoteModel = NonNullable<ReturnType<typeof useRemoteModel>>;

type Cells = LookaheadDepth["cells"];
type Abreast = LookaheadAbreastStep["abreast"];
// The rows' last press short of Max speed on one cycle: switching Max speed off returns to it.
type Shown = { cycle: string; cells: Cells; abreast: Abreast };

export function useRemoteModel() {
  const { viewed, navigate } = useWorkspace();
  const viewedPath = viewed?.path ?? null;
  const leafEntry = useEntry(viewedPath ? encodeCyclePath(viewedPath) : null);
  const { dash, isLive } = useViewedDashboard();
  const cmd = useCommand<"skip" | "sample-lookahead">("remote-control", viewed?.address ?? null);
  const [shown, setShown] = useState<Shown | null>(null);
  const isOuterView = (viewedPath?.length ?? 1) === 1;

  if (!viewedPath) return null;

  // No `settled` gate: a verb is still offered there, and this gates `RunControlButton`'s only mount.
  if (!dash) return null;
  const { lookahead } = dash;
  const cycle = encodeCyclePath(viewedPath);
  if (
    !lookahead.max_speed &&
    (shown === null ||
      shown.cycle !== cycle ||
      shown.cells !== lookahead.pressed ||
      shown.abreast !== lookahead.abreast)
  ) {
    setShown({ cycle, cells: lookahead.pressed, abreast: lookahead.abreast });
  }
  const innerHop = isLive ? dash.current_round.inner : null;
  const arm = (cells: Cells, abreast: Abreast, auto: boolean) =>
    void cmd.run("sample-lookahead", () =>
      // `cells: 1, abreast: 1` disarms; sent unclamped, the walk clamps it to `lookahead.ceiling`.
      commands["set-sample-lookahead"]({ ...cycleAddress(viewedPath), cells, abreast, auto }),
    );
  // A press on one row keeps the other row and the every-round switch where they stand.
  const armCells = (raw: string) => {
    const pressed = lookahead.depths.find((d) => String(d.cells) === raw);
    if (pressed === undefined || pressed.cells === lookahead.pressed) return;
    arm(pressed.cells, lookahead.abreast, lookahead.auto);
  };
  const armAbreast = (raw: string) => {
    const pressed = lookahead.abreasts.find((s) => String(s.abreast) === raw);
    if (pressed === undefined || pressed.abreast === lookahead.abreast) return;
    arm(lookahead.pressed, pressed.abreast, lookahead.auto);
  };
  // Off with no press seen on this cycle (the page opened at Max speed): one at a time on both.
  const toggleMaxSpeed = () => {
    if (!lookahead.max_speed) arm("max", "all", lookahead.auto);
    else if (shown === null || shown.cycle !== cycle) arm(1, 1, lookahead.auto);
    else arm(shown.cells, shown.abreast, lookahead.auto);
  };

  return {
    babysat: leafEntry?.entry_kind === "cycle" && leafEntry.state.intervened,
    inner: !isOuterView,
    innerHop,
    toOuter: () => navigate({ path: viewedPath.slice(0, 1) }),
    toInner: (campaignId: string, cycleId: string) =>
      navigate({ path: [...viewedPath, { campaignId, cycleId }] }),
    skipRefusal: dash.skip_refusal,
    caps: dash.caps.caps,
    overshoot: dash.run_limits.overshoot,
    overshot: dash.overshot,
    lookahead,
    heldCall: dash.held_call,
    providerHeld: dash.backpressure,
    arm,
    armCells,
    armAbreast,
    toggleMaxSpeed,
    skip: () =>
      void cmd.run("skip", () => commands["skip-searchpoint"](cycleAddress(viewedPath))),
    pending: cmd.pending,
    failure: cmd.failure,
  };
}
