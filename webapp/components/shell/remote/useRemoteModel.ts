"use client";
import { useMemo } from "react";
import { cycleAddress, postCommand } from "@/lib/api";
import { useCommand } from "@/lib/hooks/useCommand";
import { phaseIs } from "@/lib/run-phase";
import {
  benchStat,
  indexLineage,
  readSpend,
  runningInnerRun,
  tagValue,
} from "@/lib/derivations";
import { fmtDuration } from "@/lib/format";
import { useCycleStream } from "@/lib/poll";
import { useLineageTree } from "@/lib/lineage";
import { encodeCyclePath, pathOfAddress } from "@/lib/ids";
import { useEntry } from "@/lib/registry";
import { useLeafIsL4, useWorkspace } from "@/lib/workspace";

export type RemoteModel = NonNullable<ReturnType<typeof useRemoteModel>>;

export function useRemoteModel() {
  const {
    cycleId,
    viewedPath,
    drillInto,
    backToOuter,
  } = useWorkspace();
  const leafIsL4 = useLeafIsL4();
  const leafEntry = useEntry(viewedPath ? encodeCyclePath(viewedPath) : null);
  const { dash, status } = useCycleStream();
  const cmd = useCommand<"skip" | "sample-lookahead">("remote-control");
  const isOuterView = (viewedPath?.length ?? 1) === 1;
  const campaignAddress = viewedPath?.[0]?.campaignId ?? "";
  const { root: lineageRoot } = useLineageTree(
    campaignAddress,
    campaignAddress !== "" && isOuterView && leafIsL4,
  );
  const innerRun = useMemo(() => runningInnerRun(indexLineage(lineageRoot)), [lineageRoot]);

  if (!viewedPath || !cycleId) return null;

  // No `settled` gate: a verb is still offered there, and this gates `RunControlButton`'s only mount.
  if (!dash) return null;
  const terminal = phaseIs(dash.run_phase, "settled");
  const skipRefusal = dash.admission.skip_refusal;
  const armRefusal = dash.admission.lookahead_refusal;

  const innerHop = innerRun ? pathOfAddress(innerRun.address).at(-1) : undefined;

  const { metered, lines } = readSpend(dash);

  const lookahead = dash.sample_lookahead;
  const autoArmed = dash.sample_lookahead_auto;
  const maxCells = dash.max_cells_in_flight;
  const inFlight = dash.in_flight;
  const allowed = dash.lookahead_allowed;
  const pickMax = dash.lookahead_pick_max;
  const { producer } = dash;
  const waitNote =
    producer.call_holds && producer.open_for_s !== null && dash.waiting_on
      ? `waiting on ${dash.waiting_on} · ${fmtDuration(producer.open_for_s)}`
      : null;
  const concurrencyUnavailable = dash.lookahead_unavailable !== "";
  const armDisabled = concurrencyUnavailable || armRefusal !== "" || cmd.pending !== null;
  const arm = (cells: number, auto: boolean) =>
    void cmd.run("sample-lookahead", () =>
      // `cells: 1` disarms; sent unclamped, the walk clamps it to `max_cells_in_flight`.
      postCommand("set-sample-lookahead", { ...cycleAddress(viewedPath), cells, auto }),
    );
  const armCells = (raw: string) => {
    const cells = Number(raw);
    if (cells === lookahead && !autoArmed) return;
    arm(cells, false);
  };
  const depthSegments = Array.from({ length: maxCells }, (_, i) => {
    const n = i + 1;
    const fill: "full" | "part" | undefined =
      n <= inFlight ? "full" : n <= allowed ? "part" : undefined;
    return {
      value: String(n),
      label: String(n),
      fill,
      disabled: armDisabled || n > pickMax,
      title: n === 1 ? "One at a time" : `Hold ${n} in flight`,
    };
  });

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
    skipRefusal,
    metered,
    lines,
    budget: dash.run_limits.budget,
    share: dash.allotment,
    benchLiftPerUsd:
      leafEntry?.level === "course" ? (benchStat(leafEntry)?.lift_per_usd ?? null) : null,
    lookahead,
    autoArmed,
    inFlight,
    allowed,
    most: dash.lookahead_most,
    scoringNow: inFlight > 0 || allowed > 0,
    cellReserve: dash.cell_reserve_usd,
    moneyHold: dash.lookahead_money_hold,
    pickMax,
    waitNote,
    providerHeld: dash.backpressure,
    concurrencyUnavailable: dash.lookahead_unavailable,
    concurrencyTitle: dash.lookahead_explained,
    // A mode, not a press, so it is offered on a paused run too: it survives the relaunch.
    autoDisabled: concurrencyUnavailable || terminal || cmd.pending !== null,
    depthSegments,
    arm,
    armCells,
    skip: () =>
      void cmd.run("skip", () => postCommand("skip-searchpoint", cycleAddress(viewedPath))),
    pending: cmd.pending,
    failure: cmd.failure,
  };
}
