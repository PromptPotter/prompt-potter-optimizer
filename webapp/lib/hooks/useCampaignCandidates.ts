"use client";
// One course's subtree of candidates — what the sidebar opens under a cycle row.
//
// Rides `/tree`, which answers a course, its candidates, and every course hanging
// off them in ONE conditional round-trip. The alternative — a `dashboard.json`
// fetch per cycle — is the same data N times over, and the sidebar shows many
// cycles at once.
//
// Deliberately NOT `useLineageOverlay`: that provider is the VIEWED campaign's, and
// it carries the what-if / lens / sample-set masks. The sidebar names candidates for
// any campaign and must show what the run actually did, not what a mask says it
// would have done under another formula. Both normalize through the one
// `derivations/lineage-candidates.ts`, so a candidate is named and keyed identically.
//
// Lazy + per-path, same discipline as useForest: nothing fetches until a cycle's
// candidates are open, and a stamped key means one campaign's rows never bleed into
// another's.

import { useCallback, useRef, useState } from "react";
import { fetchLineageTree } from "../api";
import {
  candidatesByCourse,
  forkAttempts,
  type LineageCandidate,
} from "../derivations";
import { encodeCyclePath, type CyclePath } from "../ids";
import { useAuthGate } from "../auth-context";
import { usePoll } from "./usePoll";

// Course-levels to expand — the campaign's forks, their forks, and the L4 inner
// runs filed under any candidate. Each level costs one ledger scan per course.
const SIDEBAR_DEPTH = 3;

export interface CampaignCandidates {
  // cycle_id → its candidates, origin (C0) first, in round order.
  byCycle: ReadonlyMap<string, LineageCandidate[]>;
  // fork cycle_id → its place on the campaign's ONE timeline. A fork is served as a
  // CANDIDATE of the course it was cut in (it is an attempt, not a container for one), so
  // this is how a surface holding a fork from the flat `/cycles` registry asks what the
  // fork is called — `C1.4`, the fourth attempt — and what it was cut from.
  forkAttempt: ReadonlyMap<string, LineageCandidate>;
  loaded: boolean;
  // The fetch resolved but FAILED. Distinct from `loaded` with no rows, and the
  // distinction is load-bearing: a course whose candidates couldn't be read is not a
  // course that never ran, and rendering the second for the first reports a broken
  // read as a measurement.
  failed: boolean;
}

const EMPTY: CampaignCandidates = {
  byCycle: new Map(),
  forkAttempt: new Map(),
  loaded: false,
  failed: false,
};

interface Loaded extends CampaignCandidates {
  key: string | null;
}

// `path` addresses the ROOT COURSE whose subtree the rows render.
export function useCampaignCandidates(path: CyclePath, enabled: boolean): CampaignCandidates {
  const { authed, onAuthError } = useAuthGate();
  const key = enabled ? encodeCyclePath(path) : null;

  const [loaded, setLoaded] = useState<Loaded>({ ...EMPTY, key: null });
  // Last-Modified validator, keyed to the query so a 304 only ever keeps the tree
  // it was issued against.
  const imsRef = useRef<{ key: string; value: string | null }>({ key: "", value: null });

  const tick = useCallback(
    async (signal: AbortSignal) => {
      if (key === null) return;
      const ims = imsRef.current.key === key ? imsRef.current.value : null;
      try {
        const res = await fetchLineageTree(path, { depth: SIDEBAR_DEPTH }, ims, signal);
        if (signal.aborted) return;
        imsRef.current = { key, value: res.lastModified ?? ims };
        // 304 = nothing changed; keep the rows we have rather than blanking them.
        if (res.kind !== "ok") {
          setLoaded((prev) => (prev.key === key ? prev : { ...EMPTY, key, failed: true }));
          return;
        }
        setLoaded({
          key,
          byCycle: candidatesByCourse(res.data),
          forkAttempt: forkAttempts(res.data),
          loaded: true,
          failed: false,
        });
      } catch (e) {
        if (signal.aborted) return;
        onAuthError(e);
        // Keep last-good only within the same query; a failed tick for a fresh one
        // reports FAILED, never `loaded` with zero rows — that read as "this course
        // never ran" and put a fetch error where a measurement goes.
        setLoaded((prev) =>
          prev.key === key ? { ...prev, key } : { ...EMPTY, key, failed: true },
        );
      }
    },
    // `path` is rebuilt per render; `key` is its stable identity.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [key, onAuthError],
  );

  usePoll(tick, {
    intervalMs: 5000,
    tickOnFocus: true,
    enabled: key !== null && authed,
  });

  if (key === null) return EMPTY;
  return loaded.key === key ? loaded : EMPTY;
}
