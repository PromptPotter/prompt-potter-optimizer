"use client";
// ONE campaign's served tree — what the sidebar renders under a root course row.
//
// Deliberately NOT `useLineageOverlay`: that provider is the VIEWED campaign's and carries
// the what-if / lens / sample-set masks. The sidebar names candidates for any campaign and
// must show what the run did, not what a mask says it would have done under another
// formula. Both walk the same served shape, so a candidate is named identically on both.
//
// Lazy + per-path: nothing fetches until a course is open, and a stamped key means one
// campaign's rows never bleed into another's.

import { useCallback, useRef, useState } from "react";
import { fetchLineageTree } from "../api";
import type { LineageNode } from "../api";
import { encodeCyclePath, type CyclePath } from "../ids";
import { useAuthGate } from "../auth-context";
import { usePoll } from "./usePoll";

// ONE level is the whole tree: a fork is not a course, so it costs no level, and the only
// nesting left is a candidate's L4 inner runs. Measured — depth 1 and depth 3 return
// byte-identical trees.
const SIDEBAR_DEPTH = 1;

export interface CampaignTree {
  // The root course of the requested path, whole. Null until the first fetch resolves.
  root: LineageNode | null;
  loaded: boolean;
  // The fetch resolved but FAILED — load-bearing, and distinct from `loaded` with no rows:
  // a course whose tree couldn't be read is not a course that never ran.
  failed: boolean;
}

const EMPTY: CampaignTree = { root: null, loaded: false, failed: false };

interface Loaded extends CampaignTree {
  key: string | null;
}

// `path` addresses the ROOT COURSE whose subtree the rows render.
export function useCampaignTree(path: CyclePath, enabled: boolean): CampaignTree {
  const { authed, onAuthError } = useAuthGate();
  const key = enabled ? encodeCyclePath(path) : null;

  const [loaded, setLoaded] = useState<Loaded>({ ...EMPTY, key: null });
  // Last-Modified validator, keyed to the query so a 304 only ever keeps the tree it was
  // issued against.
  const imsRef = useRef<{ key: string; value: string | null }>({ key: "", value: null });

  const tick = useCallback(
    async (signal: AbortSignal) => {
      if (key === null) return;
      const ims = imsRef.current.key === key ? imsRef.current.value : null;
      try {
        const res = await fetchLineageTree(path, { depth: SIDEBAR_DEPTH }, ims, signal);
        if (signal.aborted) return;
        imsRef.current = { key, value: res.lastModified ?? ims };
        // 304 = nothing changed; keep the tree we have rather than blanking it.
        if (res.kind !== "ok") {
          setLoaded((prev) => (prev.key === key ? prev : { ...EMPTY, key, failed: true }));
          return;
        }
        setLoaded({ key, root: res.data, loaded: true, failed: false });
      } catch (e) {
        if (signal.aborted) return;
        onAuthError(e);
        // Keep last-good only within the same query; a failed tick for a fresh one reports
        // FAILED, never `loaded` with no tree — that puts a fetch error where a
        // measurement goes.
        setLoaded((prev) => (prev.key === key ? { ...prev, key } : { ...EMPTY, key, failed: true }));
      }
    },
    // `path` is rebuilt per render; `key` is its stable identity.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [key, onAuthError],
  );

  usePoll(tick, { intervalMs: 5000, tickOnFocus: true, enabled: key !== null && authed });

  if (key === null) return EMPTY;
  return loaded.key === key ? loaded : EMPTY;
}
