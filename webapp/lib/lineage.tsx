"use client";
// THE served lineage — one fetch owner for the whole app.
//
// This replaces a pair that fetched the same bodies independently: a root-scoped masked
// provider (the forest, the bars) and a path-scoped mask-free hook (the sidebar, the L4
// samples panel), with no shared cache. One campaign could have up to four poll loops over
// three URLs, and the two disagreed on a candidate's LABEL, because the root tree renumbers
// a fork's contributions onto the campaign timeline while a fork's own tree keeps its
// private counter. The server now carries both (`label` + `course_label`), so one tree
// answers everyone and the second fetch has nothing left to justify it.
//
// Two things were claimed to require the split, and neither survived:
//
//   - "the sidebar must be mask-free." A masked body is a strict SUPERSET: the server's
//     overlay only ever writes `divergence` / `divergent` / `lens_value` /
//     `sample_set_accuracy` / `sample_set_n`, and never touches `accuracy`,
//     `composite_fitness`, `is_winner`, `theta`, `best_accuracy`, `origin_accuracy` or
//     `run_phase` — i.e. everything the sidebar reads. The property came from the field
//     set, not from a second request.
//   - "the labels would not join." They join on `course_label`, which is the minting
//     course's own position and is exactly what a per-cycle `dashboard.json` speaks.
//
// Shape: a keyed store of trees, ref-counted by interested subscribers, one poll per live
// key. `useLineageTree(path, enabled)` is any campaign's tree; `useViewedLineage()` is the
// viewed campaign's, carrying the lens / what-if / sample-set masks it alone owns.

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  useSyncExternalStore,
} from "react";
import { fetchLineageTree } from "@/lib/api";
import type { LineageNode } from "@/lib/api/types";
import { nodeAt, walkCourses } from "@/lib/derivations";
import { useCandidatesState } from "@/components/candidates/candidates-store";
import { formulaFromWeights } from "@/components/candidates/fitness-bars";
import { useAuthGate } from "@/lib/auth-context";
import { useDebounced } from "@/lib/hooks/useDebounced";
import { usePoll } from "@/lib/hooks/usePoll";
import { encodeCyclePath, rootCycleId, type CyclePath } from "@/lib/ids";
import { useRevalidation } from "@/lib/revalidate";
import { useSelection } from "@/lib/SelectionContext";

// One cadence for every subscribed tree. It is the sidebar's old interval, not the
// overlay's event-driven revalidation: the server validator is the subtree's ledger mtime,
// which bumps the moment a candidate is minted, so a 5 s tick sees an in-flight candidate
// at its start and costs a 304 the rest of the time. The overlay's `dashChangeKey` proxy
// ("the dashboard moved, so the tree might have") is gone — it was guessing at what the
// validator now answers directly, and it forced this provider to sit under the dashboard
// stream for no other reason.
const POLL_MS = 5000;

// Short label per preset lens for the mask tag.
const LENS_LABELS: Record<string, string> = {
  "score:accuracy": "Accuracy",
  "abort:epsilon_off": "No ε-elim",
  "abort:lock_in_off": "No lock-in",
  "abort:all_off": "No abort",
};

export interface CampaignTree {
  // The root course of the requested path, whole. Null until the first fetch resolves.
  root: LineageNode | null;
  loaded: boolean;
  // The fetch resolved but FAILED — load-bearing, and distinct from `loaded` with no rows:
  // a course whose tree couldn't be read is not a course that never ran.
  failed: boolean;
}

const EMPTY: CampaignTree = { root: null, loaded: false, failed: false };

export interface ViewedLineage {
  // THE served genealogy, rooted at the viewed campaign's root course.
  tree: LineageNode | null;
  // The PRESET lens dropdown value: "" (off), "score:<formula>", or "abort:<variant>".
  // The What-If card overrides it as the master when open.
  lens: string;
  setLens: (lens: string) => void;
  // A mask actually produced a divergence (drives the red tag) + its short label;
  // whether the What-If card is the active master (preset dropdown overridden).
  maskActive: boolean;
  maskLabel: string;
  whatifActive: boolean;
}

interface Store {
  entries: Map<string, CampaignTree>;
  // Ref-counted interest per key. A key with no subscribers is not polled — that is what
  // keeps a collapsed sidebar free, and what stops a dead campaign's loop from outliving
  // the row that opened it.
  subscribe: (key: string, path: CyclePath) => () => void;
  viewed: ViewedLineage;
}

const LineageContext = createContext<Store | null>(null);

// Who currently wants which tree — an EXTERNAL store, read through `useSyncExternalStore`.
//
// It has to be external rather than `useState` because the writers are mount/unmount
// effects: a sidebar row opening is a subscription, and calling setState synchronously from
// an effect body is the cascading-render pattern React (and `react-hooks/set-state-in-effect`)
// rejects. The same shape `useLocalStorage` already uses here — mutate, then notify.
//
// The version counter is what feeds `usePoll`'s `revalidateOn`, so a newly opened row
// fetches within a frame instead of waiting out the 5 s interval.
// It also owns each key's ETag. Nothing renders a validator, and it must die exactly when
// its body does — replaying an ETag against a tree we no longer hold would 304 into an
// empty entry. Keeping both in the registry makes that one deletion instead of two that
// have to be remembered together.
interface Registry {
  subscribe: (key: string, path: CyclePath) => () => void;
  onVersionChange: (listener: () => void) => () => void;
  version: () => number;
  live: () => [string, CyclePath][];
  has: (key: string) => boolean;
  etag: (key: string) => string | null;
  setEtag: (key: string, value: string | null) => void;
}

function createRegistry(onDrop: (key: string) => void): Registry {
  const counts = new Map<string, { count: number; path: CyclePath }>();
  const etags = new Map<string, string>();
  const listeners = new Set<() => void>();
  let version = 0;
  const bump = (): void => {
    version += 1;
    for (const l of listeners) l();
  };
  return {
    subscribe(key, path) {
      const cur = counts.get(key);
      counts.set(key, { count: (cur?.count ?? 0) + 1, path });
      bump();
      return () => {
        const entry = counts.get(key);
        if (!entry) return;
        if (entry.count <= 1) {
          counts.delete(key);
          etags.delete(key);
          onDrop(key);
        } else {
          counts.set(key, { count: entry.count - 1, path: entry.path });
        }
        bump();
      };
    },
    onVersionChange(listener) {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    version: () => version,
    live: () => [...counts].map(([k, v]) => [k, v.path]),
    has: (key) => counts.has(key),
    etag: (key) => etags.get(key) ?? null,
    setEtag: (key, value) => {
      if (value) etags.set(key, value);
      else etags.delete(key);
    },
  };
}

// The viewed campaign's key carries its mask; every other key is the bare address. Kept in
// one function so a subscriber and the fetcher can never disagree about what they named.
function treeKey(path: CyclePath, lens: string | null, samples: string): string {
  const addr = encodeCyclePath(path);
  return lens || samples ? `${addr}|${lens ?? ""}|${samples}` : addr;
}

// Every (course, candidate) pair in the tree — the one walk the overlay maps ride.
function candidatePairs(tree: LineageNode | null): { courseId: string; cand: LineageNode }[] {
  if (!tree) return [];
  const out: { courseId: string; cand: LineageNode }[] = [];
  for (const course of walkCourses(tree)) {
    for (const cand of course.children) {
      if (cand.kind === "candidate") out.push({ courseId: course.id, cand });
    }
  }
  return out;
}

export function LineageProvider({
  campaignId,
  cycleId,
  children,
}: {
  campaignId: string | null;
  cycleId: string | null;
  children: React.ReactNode;
}) {
  const { authed, onAuthError } = useAuthGate();
  const reval = useRevalidation();

  const [lens, setLens] = useState<string>("");
  // Lens is a campaign-level view choice; reset it when the campaign changes so a preset /
  // What-If selection can't leak across campaigns (this provider lives at the shell root,
  // persisting across cycle + tab switches).
  const [prevCampaign, setPrevCampaign] = useState(campaignId);
  if (campaignId !== prevCampaign) {
    setPrevCampaign(campaignId);
    setLens("");
  }

  // The What-If card is the master: when open, the tree follows its live selection +
  // weights (the SAME weighted criterion as the bars), debounced so a continuous drag
  // doesn't spam the fetch.
  const { selected: whatifSelected, weights: whatifWeights, showWhatIf } = useCandidatesState();
  // The fixed sample-set chip is ALSO a mask: re-score accuracy over only those ids
  // backend-side. Serialized to a stable string so a same-content set doesn't re-fire.
  const { sampleSet } = useSelection();
  const samplesParam = useMemo(
    () => (sampleSet && sampleSet.length > 0 ? sampleSet : null),
    [sampleSet],
  );
  const samplesKey = samplesParam ? samplesParam.join(",") : "";
  const liveWhatifFormula = useMemo(
    () => (showWhatIf ? formulaFromWeights(whatifSelected, whatifWeights) : null),
    [showWhatIf, whatifSelected, whatifWeights],
  );
  const whatifFormula = useDebounced(liveWhatifFormula, 250);
  // One lens value drives the fetch: the What-If `score:` formula when its card is master,
  // else the preset dropdown (already a `score:`/`abort:` value).
  const lensParam = useMemo(() => {
    if (showWhatIf) return whatifFormula ? `score:${whatifFormula}` : null;
    return lens || null;
  }, [showWhatIf, whatifFormula, lens]);
  const maskLabel = showWhatIf
    ? "What-If"
    : samplesParam
      ? "Sample set"
      : (LENS_LABELS[lens] ?? "");

  // The viewed tree is rooted at the campaign's ROOT course, and the server's recursion
  // reaches every fork below it — so selecting a fork must not re-root (and re-fetch) it.
  // The memo keys on the derived root id, not `cycleId`: a same-campaign cycle switch must
  // keep the array's IDENTITY too, or the self-subscribe effect below re-runs, drops the
  // key's refcount to zero, and the registry deletes the body + ETag it just fetched.
  const rootId = cycleId ? rootCycleId(cycleId) : null;
  const viewedPath = useMemo<CyclePath | null>(() => {
    if (!campaignId || !rootId) return null;
    return [{ campaignId, cycleId: rootId }];
  }, [campaignId, rootId]);
  const viewedKey = viewedPath ? treeKey(viewedPath, lensParam, samplesKey) : null;

  const [entries, setEntries] = useState<Map<string, CampaignTree>>(() => new Map());

  // Last subscriber left: drop the body along with the subscription. Keeping it would grow
  // unboundedly as the operator browses campaigns, and the next open refetches in one tick.
  const [registry] = useState(() =>
    createRegistry((key) => {
      setEntries((prev) => {
        if (!prev.has(key)) return prev;
        const next = new Map(prev);
        next.delete(key);
        return next;
      });
    }),
  );
  const subsVersion = useSyncExternalStore(
    registry.onVersionChange,
    registry.version,
    registry.version,
  );
  const subscribe = registry.subscribe;

  // The provider self-subscribes the viewed key: it owns the masks, so no consumer can
  // name that key correctly on its own.
  useEffect(() => {
    if (!viewedKey || !viewedPath) return;
    return subscribe(viewedKey, viewedPath);
  }, [viewedKey, viewedPath, subscribe]);

  // ONE tick for every subscribed key. Each replays its own ETag, so a quiescent campaign
  // costs one conditional request per interval and no body.
  const tick = useCallback(
    async (signal: AbortSignal) => {
      const live = registry.live();
      await Promise.all(
        live.map(async ([key, path]) => {
          const prior = registry.etag(key);
          // The viewed key is the only one that carries a mask; every other subscriber
          // asked for the raw read, and its key says so.
          const masked = key === viewedKey;
          try {
            const res = await fetchLineageTree(
              path,
              masked ? { lens: lensParam, samples: samplesParam } : {},
              prior,
              signal,
            );
            if (signal.aborted) return;
            // A key unsubscribed mid-flight must not be resurrected by its own response.
            if (!registry.has(key)) return;
            registry.setEtag(key, res.validator);
            // 304 — the body we hold is still current; nothing to re-render.
            if (res.kind !== "ok") return;
            setEntries((prev) => {
              const next = new Map(prev);
              next.set(key, { root: res.data, loaded: true, failed: false });
              return next;
            });
          } catch (e) {
            if (signal.aborted) return;
            onAuthError(e);
            // A failed tick invalidates the validator: the next attempt must ask for a
            // full body rather than 304 into a tree it never received.
            registry.setEtag(key, null);
            setEntries((prev) => {
              if (!registry.has(key)) return prev;
              // Keep a last-good body; report FAILED only when there is nothing to keep.
              // `loaded` with no tree would put a fetch error where a measurement goes.
              if (prev.get(key)?.loaded) return prev;
              const next = new Map(prev);
              next.set(key, { ...EMPTY, failed: true });
              return next;
            });
          }
        }),
      );
    },
    // `viewedKey` moves only on a campaign switch or a mask change — both of which the
    // other two deps already track, and both of which should re-tick anyway.
    [registry, viewedKey, lensParam, samplesParam, onAuthError],
  );

  usePoll(tick, {
    intervalMs: POLL_MS,
    tickOnFocus: true,
    enabled: authed,
    revalidateOn: subsVersion + reval,
  });

  const viewedTree = viewedKey ? (entries.get(viewedKey)?.root ?? null) : null;
  const pairs = useMemo(() => candidatePairs(viewedTree), [viewedTree]);

  // "Active" (red tag) only when the served tree actually carries a divergence — NOT merely
  // because a lens/chip is requested.
  const maskActive = useMemo(
    () => pairs.some(({ cand }) => cand.divergence !== null || cand.divergent),
    [pairs],
  );

  const viewed = useMemo<ViewedLineage>(
    () => ({ tree: viewedTree, lens, setLens, maskActive, maskLabel, whatifActive: showWhatIf }),
    [viewedTree, lens, maskActive, maskLabel, showWhatIf],
  );

  const value = useMemo<Store>(() => ({ entries, subscribe, viewed }), [entries, subscribe, viewed]);

  return <LineageContext.Provider value={value}>{children}</LineageContext.Provider>;
}

function useStore(): Store {
  const ctx = useContext(LineageContext);
  if (ctx === null) throw new Error("useLineage* must be used within a LineageProvider");
  return ctx;
}

// The VIEWED campaign's tree plus its mask state — the surfaces that render the
// counterfactual (the bars, the forest, the What-If tag).
export function useViewedLineage(): ViewedLineage {
  return useStore().viewed;
}

// ANY campaign's tree, by address. Subscribing is what starts its poll; `enabled` false
// (a collapsed sidebar row) subscribes to nothing and fetches nothing.
export function useLineageTree(path: CyclePath, enabled: boolean): CampaignTree {
  const { entries, subscribe } = useStore();
  const addr = encodeCyclePath(path);
  const key = enabled ? addr : null;

  useEffect(() => {
    if (key === null) return;
    // Rebuild the path from the key's own address so the effect doesn't depend on the
    // caller's array identity (`path` is rebuilt every render).
    return subscribe(key, path);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, subscribe]);

  if (key === null) return EMPTY;
  const entry = entries.get(key);
  if (!entry) return EMPTY;
  return { root: entry.root, loaded: entry.loaded, failed: entry.failed };
}

// Per-course divergence facts for the round axis — which rounds of `path` are a divergence
// point vs. inside the counterfactual subtree. Pure derivation over the served tree;
// surfaces share it so they mark identically. The marker rides the round's WINNER (the
// candidate the lens would have replaced), and every candidate of a counterfactual round
// carries `divergent`.
export function divergenceRoundsFor(
  viewed: ViewedLineage,
  path: CyclePath | null,
): { points: ReadonlySet<number>; subtree: ReadonlySet<number> } {
  const points = new Set<number>();
  const subtree = new Set<number>();
  if (!path || !viewed.tree) return { points, subtree };
  // Addressed, not scanned: a bare cycle id names more than one course (inner ids repeat
  // across sandboxes), and `find` would answer with whichever came first.
  const course = nodeAt(viewed.tree, path);
  for (const cand of course?.children ?? []) {
    if (cand.round == null) continue;
    if (cand.divergence) points.add(cand.round);
    if (cand.divergent) subtree.add(cand.round);
  }
  return { points, subtree };
}
