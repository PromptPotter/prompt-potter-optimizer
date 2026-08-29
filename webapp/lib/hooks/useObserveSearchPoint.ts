"use client";
// The OBSERVE resolution, in one place: which of the three searchpoint states
// (`best` / `latest` / `selected`) is showing, which are offerable, and the
// read-only resolved config for the active one.
//
// Two hosts read it — the pipeline node detail (`BackendNodeDetail`) and the
// chat's run card — and they must not disagree about what "best" means, so the
// walk, the availability rules and the auto-pick live here rather than being
// re-spelled per surface.
//
// ONE round file is fetched: the ACTIVE state's. Availability is decided from
// served targets instead (does such a candidate exist), so the buttons don't
// flicker with a fetch and a state can be offered before its file has landed.

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
  // Which states may be offered. `selected` is false whenever nothing is selected
  // elsewhere — the host renders no button for it rather than a disabled one.
  avail: Record<ObserveState, boolean>;
  setPref: (s: ObserveState) => void;
  // The active state's resolved config; null while its round file is loading, and
  // null-forever when nothing has been measured yet. The host must SAY which, so
  // `loading` is reported beside it rather than folded into the same absence.
  cfg: ObserveConfig | null;
  loading: boolean;
  // The active state's target — null while `latest` is resolving to the in-flight
  // candidate, which has no round file and no closed position.
  target: ObserveTarget | null;
}

// `enabled` false parks the whole resolution — no round-file fetch, no offerable
// state. It exists for the ONE host that renders during setup (a draft is being
// authored, so there is no searchpoint to observe yet) and must not spend a fetch
// on whichever cycle happens to still be selected behind the draft.
export function useObserveSearchPoint(
  nodeId?: string | null,
  enabled = true,
): ObserveSearchPoint {
  const { dash, isLive } = useDashboard();
  const { candidate: selCand } = useSelection();
  // The viewed leaf comes from the synchronous workspace, NOT from `dash` (which
  // hard-nulls on a soft unit switch and stays null while `warming_up`) — reading it
  // off `dash` starves the round-file fetch and blanks the surface even though the
  // files are on disk.
  const { viewedPath } = useWorkspace();

  const liveCfg = enabled ? liveObserveConfig(dash, nodeId) : null;
  const bestTarget = enabled ? bestObserveTarget(dash) : null;
  const latestTarget = enabled ? latestClosedTarget(dash) : null;
  const selTarget: ObserveTarget | null = enabled && selCand
    ? {
        round: selCand.round,
        // The selection carries no index — it is not needed here, because the join is
        // on `courseLabel`, which the selection DOES carry.
        idx: -1,
        courseLabel: selCand.label,
        label: `selected · ${selCand.label}`,
        candidateId: selCand.candidate_id,
      }
    : null;
  // `latest` resolves to the in-flight candidate only while the cycle is actually
  // running: `current_round` candidates linger in dashboard.json after a stop, so
  // gating on their presence would keep offering a stale "now".
  const liveLatest = isLive && !!liveCfg;

  const avail: Record<ObserveState, boolean> = {
    best: !!bestTarget,
    latest: liveLatest || !!latestTarget,
    selected: !!selTarget,
  };

  // The explicit pick, dropped whenever the selection changes so the surface
  // re-follows it (render-phase guarded — a `useEffect` reset paints one stale frame).
  const [pref, setPref] = useState<ObserveState | null>(null);
  const selKey = selCand?.candidate_id ?? null;
  const [prevSel, setPrevSel] = useState<string | null>(selKey);
  if (selKey !== prevSel) {
    setPrevSel(selKey);
    setPref(null);
  }

  // Auto-pick: follow an explicit selection, else show what the run is doing now
  // while it runs, else the parent.
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
