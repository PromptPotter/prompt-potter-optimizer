"use client";
// One campaign's candidates, per cycle — what the sidebar opens under a cycle row.
//
// Rides `/lineage`, which already answers "every cycle in this campaign, with each
// round's candidates" in ONE conditional round-trip. The alternative — a
// `dashboard.json` fetch per cycle — is the same data N times over, and the sidebar
// shows many cycles at once.
//
// Deliberately NOT `useLineageOverlay`: that provider is the VIEWED campaign's, and
// it carries the what-if / lens / sample-set masks. The sidebar names candidates for
// any campaign and must show what the run actually did, not what a mask says it
// would have done under another formula.
//
// Lazy + per-path, same discipline as useForest: nothing fetches until a cycle's
// candidates are open, and a stamped key means one campaign's rows never bleed into
// another's.

import { useCallback, useRef, useState } from "react";
import { fetchCampaignLineage } from "../api";
import {
  labelByCandidateId,
  lineageCandidates,
  type LineageCandidate,
} from "../derivations";
import { encodeCyclePath, type CyclePath } from "../ids";
import { useAuthGate } from "../auth-context";
import { usePoll } from "./usePoll";

export interface CampaignCandidates {
  // cycle_id → its candidates, origin (C0) first, in round order.
  byCycle: ReadonlyMap<string, LineageCandidate[]>;
  // cycle_id → the label of the candidate this cycle was CUT FROM (`C2.2`), for a
  // fork whose cut named one. Absent for roots, and for divergence / rebase / sweep
  // / diag cuts, which attach at round level only — nothing on disk names their
  // candidate, so nothing here invents one.
  forkFromLabel: ReadonlyMap<string, string>;
  loaded: boolean;
  // The fetch resolved but FAILED. Distinct from `loaded` with no rows, and the
  // distinction is load-bearing: a course whose candidates couldn't be read is not a
  // course that never ran, and rendering the second for the first reports a broken
  // read as a measurement.
  failed: boolean;
}

const EMPTY: CampaignCandidates = {
  byCycle: new Map(),
  forkFromLabel: new Map(),
  loaded: false,
  failed: false,
};

interface Loaded extends CampaignCandidates {
  key: string | null;
}

export function useCampaignCandidates(
  campaignId: string,
  at: CyclePath,
  enabled: boolean,
): CampaignCandidates {
  const { authed, onAuthError } = useAuthGate();
  const key = enabled ? `${encodeCyclePath(at)}|${campaignId}` : null;

  const [loaded, setLoaded] = useState<Loaded>({ ...EMPTY, key: null });
  // Last-Modified validator, keyed to the query so a 304 only ever keeps the tree
  // it was issued against.
  const imsRef = useRef<{ key: string; value: string | null }>({ key: "", value: null });

  const tick = useCallback(
    async (signal: AbortSignal) => {
      if (key === null) return;
      const ims = imsRef.current.key === key ? imsRef.current.value : null;
      try {
        const res = await fetchCampaignLineage(campaignId, null, null, ims, signal, at);
        if (signal.aborted) return;
        imsRef.current = { key, value: res.lastModified ?? ims };
        // 304 = nothing changed; keep the rows we have rather than blanking them.
        if (res.kind !== "ok") {
          setLoaded((prev) => (prev.key === key ? prev : { ...EMPTY, key, failed: true }));
          return;
        }
        const cycles = res.data.cycles;
        const labelById = labelByCandidateId(cycles);
        const byCycle = new Map<string, LineageCandidate[]>();
        const forkFromLabel = new Map<string, string>();
        for (const c of cycles) {
          byCycle.set(c.cycle_id, lineageCandidates(c));
          const cutLabel = c.fork_from_candidate_id
            ? labelById.get(c.fork_from_candidate_id)
            : undefined;
          if (cutLabel) forkFromLabel.set(c.cycle_id, cutLabel);
        }
        setLoaded({ key, byCycle, forkFromLabel, loaded: true, failed: false });
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
    // `at` is rebuilt per render; `key` is its stable identity.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [key, campaignId, onAuthError],
  );

  usePoll(tick, {
    intervalMs: 5000,
    tickOnFocus: true,
    enabled: key !== null && authed,
  });

  if (key === null) return EMPTY;
  return loaded.key === key ? loaded : EMPTY;
}
