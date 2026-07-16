"use client";
// One store's forest — its campaigns + cycles + own active pointer, fetched at a
// CyclePath. `at` is the chain of cycles to descend INTO: `[]` is the tenant's own
// tree, one hop is an L4 (`promptpotter-self`) cycle's inner fan-out, two is an L5
// descendant. A sandbox is structurally a normal projects tree, so this is the same
// pair of reads at every depth — nothing here is depth-aware.
//
// Polled on a slow (~5 s) cadence only while the node is expanded: a live inner
// pointer advances as candidates complete, so the ● marker moves and finished runs
// flip to `terminal` + show `best_accuracy` without a reload.
//
// ONE FOREST PER PATH, not per mount. The backend already answers a whole store in
// one `/campaigns`+`/cycles` pair at any depth, but the client threw that away: for
// one viewed L4 course the sidebar, the candidates card, the samples pane and the
// hard-samples pointer each mounted this hook on the SAME path and each ran its own
// 5 s pair. So the state lives in a module store keyed on the encoded path — same
// spirit as `lib/poll.tsx`'s per-`unitKey` `lastModifiedRef` — and every mount
// subscribes to it. The hook's signature is unchanged; N mounts of one path are one
// poll, and mounts of different paths never see each other's rows.

import { useCallback, useSyncExternalStore } from "react";
import { fetchCampaigns, fetchCycles } from "../api";
import type { CampaignSummary, CycleListEntry } from "../api";
import { encodeCyclePath, type CyclePath } from "../ids";
import { useAuthGate } from "../auth-context";
import { usePoll } from "./usePoll";

export interface ForestState {
  campaigns: CampaignSummary[];
  cycles: CycleListEntry[];
  // The resolved store's OWN pointer — the active session at the top level, the
  // inner loop running right now inside a sandbox. Raw: liveness is `run_phase`'s
  // job, so ask `isPathLive` rather than assuming a pointer means running.
  activeCampaignId: string | null;
  activeCycleId: string | null;
  // False until the first fetch for this path resolves.
  loaded: boolean;
}

const EMPTY: ForestState = {
  campaigns: [],
  cycles: [],
  activeCampaignId: null,
  activeCycleId: null,
  loaded: false,
};

const POLL_MS = 5000;
// Every subscriber keeps its own timer (that is what `usePoll` owns — the hidden-tab
// pause and the focus wake are per-mount facts), and they fire out of phase. This is
// what makes them share the RESULT instead of racing: a tick landing inside another
// mount's window is that fetch, not a new one, so the wire rate stays ~one pair per
// interval however many surfaces are open. Just under POLL_MS, so ordinary timer
// drift never swallows a legitimate tick.
const COALESCE_MS = 4000;

// One path's forest, and everyone watching it. Dropped when the last watcher leaves —
// a remount refetches rather than showing a store that has since moved on.
interface Entry {
  state: ForestState;
  listeners: Set<() => void>;
  inflight: boolean;
  // `Date.now()` of the last fetch START — the coalescing window's anchor.
  lastStart: number;
}

const forests = new Map<string, Entry>();

function publish(entry: Entry, state: ForestState): void {
  entry.state = state;
  for (const l of entry.listeners) l();
}

async function refresh(
  key: string,
  at: CyclePath,
  onAuthError: (e: unknown) => void,
  signal: AbortSignal,
): Promise<void> {
  // Only ever refresh a forest somebody is watching — `subscribe` owns creation, so a
  // tick that outran its own unsubscribe finds nothing and stops rather than
  // resurrecting an entry with no listener to publish to.
  const entry = forests.get(key);
  if (!entry) return;
  const now = Date.now();
  if (entry.inflight || now - entry.lastStart < COALESCE_MS) return;
  entry.inflight = true;
  entry.lastStart = now;
  try {
    // `lifecycle: "all"` inside a sandbox: inner campaigns are machine-minted
    // instrument runs, never archived by an operator, so the default `active`
    // filter would differ from the outer list for no reason. At the top level
    // the caller owns the filter and passes `at: []` — this hook is only
    // mounted for descended nodes.
    const [campaignsRes, cyclesRes] = await Promise.all([
      fetchCampaigns(undefined, signal, "all", at),
      fetchCycles(signal, at),
    ]);
    // The entry can be dropped mid-flight (last watcher unmounted); publishing into
    // it would resurrect a forest nobody asked for.
    if (signal.aborted || forests.get(key) !== entry) return;
    publish(entry, {
      campaigns: campaignsRes.campaigns,
      cycles: cyclesRes.cycles,
      activeCampaignId: cyclesRes.active_campaign_id,
      activeCycleId: cyclesRes.active_cycle_id,
      loaded: true,
    });
  } catch (e) {
    if (signal.aborted || forests.get(key) !== entry) return;
    onAuthError(e);
    // Keep last-good once we have it; a first failure resolves to an empty forest so
    // the tree stops saying "loading". A cycle that spawned no inner campaigns has no
    // sandbox, so its hop 404s — that lands here and renders "none yet", which is
    // exactly the intended signal.
    if (!entry.state.loaded) publish(entry, { ...EMPTY, loaded: true });
  } finally {
    entry.inflight = false;
  }
}

export function useForest(at: CyclePath, enabled: boolean): ForestState {
  const { authed, onAuthError } = useAuthGate();
  const pathKey = enabled && authed ? encodeCyclePath(at) : null;

  const subscribe = useCallback(
    (onChange: () => void) => {
      if (pathKey === null) return () => {};
      // Subscribing is what brings a path's forest into being, and the last
      // unsubscribe is what ends it — so an entry exists exactly while it is watched.
      let entry = forests.get(pathKey);
      if (!entry) {
        entry = { state: EMPTY, listeners: new Set(), inflight: false, lastStart: 0 };
        forests.set(pathKey, entry);
      }
      entry.listeners.add(onChange);
      return () => {
        entry.listeners.delete(onChange);
        if (entry.listeners.size === 0) forests.delete(pathKey);
      };
    },
    [pathKey],
  );

  // Per-path by construction, so there is no stamp-key guard to run: a fresh path
  // reads its own entry, and one store's forest cannot bleed into another's.
  const getSnapshot = useCallback(
    () => (pathKey === null ? EMPTY : (forests.get(pathKey)?.state ?? EMPTY)),
    [pathKey],
  );

  const tick = useCallback(
    (signal: AbortSignal) =>
      pathKey === null ? undefined : refresh(pathKey, at, onAuthError, signal),
    // `at` is rebuilt per render; pathKey is its stable identity.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [pathKey, onAuthError],
  );

  usePoll(tick, { intervalMs: POLL_MS, tickOnFocus: true, enabled: pathKey !== null });

  return useSyncExternalStore(subscribe, getSnapshot, () => EMPTY);
}
