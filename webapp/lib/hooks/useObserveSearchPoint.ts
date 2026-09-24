"use client";
// The OBSERVE resolution (`best` / `latest` / `selected`): the one place its hosts learn what
// "best" means. Only the ACTIVE state's round file is fetched; availability reads served targets.

import { useState } from "react";
import {
  bestObserveTarget,
  candidateObserveConfig,
  latestClosedTarget,
  liveObserveConfig,
  type ObserveConfig,
  type ObserveState,
  type ObserveTarget,
} from "@/lib/derivations";
import { useDashboard } from "./useDashboard";
import { useRoundFile } from "./useRoundFile";
import { useSelection } from "@/lib/SelectionContext";
import { useWorkspace } from "@/lib/workspace";

export interface ObserveSearchPoint {
  state: ObserveState;
  // An unavailable state gets no button, never a disabled one.
  avail: Record<ObserveState, boolean>;
  setPref: (s: ObserveState) => void;
  // Null while loading AND null when nothing is measured; the host must say which (`loading`).
  cfg: ObserveConfig | null;
  loading: boolean;
  // Null while `latest` is the in-flight candidate, which has no round file.
  target: ObserveTarget | null;
}

// `enabled` false is for the setup host: no fetch on whatever cycle sits behind the draft.
export function useObserveSearchPoint(
  nodeId?: string | null,
  enabled = true,
): ObserveSearchPoint {
  const { dash, isLive } = useDashboard();
  const { candidate: selCand } = useSelection();
  // From the workspace, NOT `dash`: `dash` nulls on a unit switch and while `warming_up`,
  // which would starve the round-file fetch.
  const { viewedPath } = useWorkspace();

  const liveCfg = enabled ? liveObserveConfig(dash, nodeId) : null;
  const bestTarget = enabled ? bestObserveTarget(dash) : null;
  const latestTarget = enabled ? latestClosedTarget(dash) : null;
  const selTarget: ObserveTarget | null = enabled && selCand
    ? {
        round: selCand.round,
        // Unused: the join is on `courseLabel`.
        idx: -1,
        courseLabel: selCand.label,
        label: `selected · ${selCand.label}`,
        candidateId: selCand.candidate_id,
      }
    : null;
  // Gate on `isLive`: `current_round` candidates linger in dashboard.json after a stop.
  const liveLatest = isLive && !!liveCfg;

  const avail: Record<ObserveState, boolean> = {
    best: !!bestTarget,
    latest: liveLatest || !!latestTarget,
    selected: !!selTarget,
  };

  const [pref, setPref] = useState<ObserveState | null>(null);
  const selKey = selCand?.candidate_id ?? null;
  const [prevSel, setPrevSel] = useState<string | null>(selKey);
  if (selKey !== prevSel) {
    setPrevSel(selKey);
    setPref(null);
  }

  const auto: ObserveState = avail.selected
    ? "selected"
    : liveLatest
      ? "latest"
      : avail.best
        ? "best"
        : "latest";
  const state: ObserveState = pref && avail[pref] ? pref : auto;

  const target: ObserveTarget | null =
    state === "selected"
      ? selTarget
      : state === "best"
        ? bestTarget
        : liveLatest
          ? null
          : latestTarget;

  const file = useRoundFile(viewedPath, target?.round ?? null);
  const cfg =
    state === "latest" && liveLatest
      ? liveCfg
      : target
        ? candidateObserveConfig(file.doc, target.courseLabel, target.label, nodeId)
        : null;

  return { state, avail, setPref, cfg, loading: file.loading, target };
}
