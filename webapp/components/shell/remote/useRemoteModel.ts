"use client";
import { useMemo } from "react";
import { cycleAddress, postCommand } from "@/lib/api";
import { useCommand } from "@/lib/hooks/useCommand";
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
import { useEntry } from "@/lib/state/registry";
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
  const { lookahead, producer } = dash;
  const innerHop = innerRun ? pathOfAddress(innerRun.address).at(-1) : undefined;
  const { metered, lines } = readSpend(dash);
  const waitNote =
    producer.call_holds && producer.open_for_s !== null && dash.waiting_on
      ? `waiting on ${dash.waiting_on} · ${fmtDuration(producer.open_for_s)}`
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
      leafEntry?.level === "course" ? (benchStat(leafEntry)?.lift_per_usd ?? null) : null,
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
