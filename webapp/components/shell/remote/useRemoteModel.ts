"use client";
import { cycleAddress, postCommand } from "@/lib/api";
import { useCommand } from "@/lib/hooks/useCommand";
import { readSpend, tagValue, waitingText } from "@/lib/derivations";
import { fmtDuration } from "@/lib/format";
import { useCycleStream } from "@/lib/poll";
import { encodeCyclePath } from "@/lib/ids";
import { useEntry } from "@/lib/state/registry";
import { useWorkspace } from "@/lib/workspace";

export type RemoteModel = NonNullable<ReturnType<typeof useRemoteModel>>;

export function useRemoteModel() {
  const {
    cycleId,
    viewedPath,
    drillInto,
    backToOuter,
  } = useWorkspace();
  const leafEntry = useEntry(viewedPath ? encodeCyclePath(viewedPath) : null);
  const { dash, status, isLive } = useCycleStream();
  const cmd = useCommand<"skip" | "sample-lookahead">("remote-control");
  const isOuterView = (viewedPath?.length ?? 1) === 1;

  if (!viewedPath || !cycleId) return null;

  // No `settled` gate: a verb is still offered there, and this gates `RunControlButton`'s only mount.
  if (!dash) return null;
  const { lookahead, producer } = dash;
  const innerHop = isLive ? dash.current_round.inner : null;
  const { metered, lines } = readSpend(dash);
  const waitNote =
    producer.call_holds && producer.open_for_s !== null && dash.waiting_on
      ? `waiting on ${waitingText(dash.waiting_on)} · ${fmtDuration(producer.open_for_s)}`
      : null;
  const arm = (cells: number, auto: boolean) =>
    void cmd.run("sample-lookahead", () =>
      // `cells: 1` disarms; sent unclamped, the walk clamps it to `lookahead.ceiling`.
      postCommand("set-sample-lookahead", { ...cycleAddress(viewedPath), cells, auto }),
    );
  const armCells = (raw: string) => {
    const cells = Number(raw);
    if (cells === lookahead.depth && !lookahead.auto) return;
    arm(cells, false);
  };

  return {
    cycleId,
    datasetName: tagValue(leafEntry, "dataset") ?? undefined,
    updatedAt: dash.at_time,
    babysat: leafEntry?.level === "course" && leafEntry.state.intervened,
    offline: status === "offline",
    inner: !isOuterView,
    innerHop,
    drillInto,
    backToOuter,
    skipRefusal: dash.admission.skip_refusal,
    metered,
    lines,
    budget: dash.run_limits.budget,
    share: dash.allotment,
    benchLiftPerUsd:
      leafEntry?.level === "course" ? (leafEntry.facts.bench?.cost.lift_per_usd ?? null) : null,
    lookahead,
    waitNote,
    providerHeld: dash.backpressure,
    arm,
    armCells,
    skip: () =>
      void cmd.run("skip", () => postCommand("skip-searchpoint", cycleAddress(viewedPath))),
    pending: cmd.pending,
    failure: cmd.failure,
  };
}
