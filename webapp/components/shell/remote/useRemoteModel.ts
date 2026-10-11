"use client";
import { useMemo } from "react";
import { postSkipSearchpoint, postSetSampleLookahead } from "@/lib/api";
import { useCommand } from "@/lib/hooks/useCommand";
import { phaseIs } from "@/lib/run-phase";
import { pathOf, readSpend, runningInnerRun } from "@/lib/derivations";
import { fmtDuration } from "@/lib/format";
import { useCycleStream } from "@/lib/poll";
import { useLineageTree } from "@/lib/lineage";
import { useCycleEntry } from "@/lib/registry";
import { useLeafIsL4, useWorkspace } from "@/lib/workspace";

export type RemoteModel = NonNullable<ReturnType<typeof useRemoteModel>>;

export function useRemoteModel() {
  const {
    cycleId,
    leafCampaignId,
    leafCycleId,
    viewedPath,
    drillInto,
    backToOuter,
  } = useWorkspace();
  const leafIsL4 = useLeafIsL4();
  const leafEntry = useCycleEntry(leafCampaignId, leafCycleId);
  const { dash, status } = useCycleStream();
  const cmd = useCommand<"skip" | "sample-lookahead">("remote-control");
  const isOuterView = (viewedPath?.length ?? 1) === 1;
  const { root: lineageRoot } = useLineageTree(
    viewedPath ?? [],
    Boolean(viewedPath) && isOuterView && leafIsL4,
  );
  const innerRun = useMemo(() => runningInnerRun(lineageRoot), [lineageRoot]);

  if (!viewedPath || !cycleId) return null;

  // No `settled` gate: a verb is still offered there, and this gates `RunControlButton`'s only mount.
  if (!dash || phaseIs(dash.run_phase, "authoring")) return null;
  const terminal = phaseIs(dash.run_phase, "settled");
  const skipRefusal = dash.run_admission.skip_refusal;
  const armRefusal = dash.run_admission.lookahead_refusal;

  const innerHop = innerRun ? pathOf(innerRun).at(-1) : undefined;

  const { metered, lines, budgetUsd, budgetTokens } = readSpend(dash);

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
      postSetSampleLookahead(viewedPath, cells, auto),
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
    datasetName: leafEntry?.dataset_name,
    updatedAt: dash.wallclock_serialized_at,
    babysat: Boolean(leafEntry?.human_intervened),
    offline: status === "offline",
    inner: !isOuterView,
    innerHop,
    drillInto,
    backToOuter,
    skipRefusal,
    metered,
    lines,
    budgetUsd,
    budgetTokens,
    maxRounds: dash.run_limits.max_rounds,
    benchLiftPerUsd: dash.bench_score?.cost.lift_per_usd ?? null,
    lookahead,
    autoArmed,
    inFlight,
    allowed,
    // `null`: an optimizer declaring no `arms_per_round` has no next-round bound to quote.
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
    skip: () => void cmd.run("skip", () => postSkipSearchpoint(viewedPath)),
    pending: cmd.pending,
    failure: cmd.failure,
  };
}
