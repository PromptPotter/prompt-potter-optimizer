"use client";

import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from "react";
import { useIngestFlow, type IngestFlow } from "@/lib/hooks/useIngestFlow";
import { useCollection, type CollectionState } from "@/lib/hooks/useCollection";
import { useWorkspace } from "@/lib/workspace";

// ONE authoring thread for the whole app. There used to be three independent
// `useIngestFlow` instances — the chat tab, the New campaign modal and the
// check-in re-open pane — sharing no state, so opening the modal over a chat
// tab that already held an `awaiting-context` draft produced two live
// server-side drafts and two disabled composers. A draft is a fact about the
// operator, not about which pane is on screen, so it lives here.
interface IngestFlowValue {
  flow: IngestFlow;
  // The collection both entry points offer (origins to reuse, datasets to make
  // an origin from). Fetched once, here, rather than per surface.
  collection: CollectionState;
  // The operator is authoring rather than watching. Suppresses the bound
  // cycle's live feed and run card, so a fresh thread is not rendered over the
  // previous campaign's activity. Cleared on mint.
  composing: boolean;
  // Begin a new authoring thread — the "New campaign" gesture, wherever it is
  // pressed from.
  startNew: () => void;
  // Bumped once per minted campaign. Selecting the new cycle is universal and
  // happens here; a host with its own landing side effect (the phone leaving its
  // list screen) guards on this rather than owning a second mint callback.
  mintCount: number;
}

const Ctx = createContext<IngestFlowValue | null>(null);

export function IngestFlowProvider({ children }: { children: ReactNode }) {
  const [composing, setComposing] = useState(false);
  const [mintCount, setMintCount] = useState(0);
  const collection = useCollection();
  const { selectCycle } = useWorkspace();
  const flow = useIngestFlow({
    onMint: (sel) => {
      setComposing(false);
      setMintCount((n) => n + 1);
      // start-checkin returns the (campaign, cycle) — land on it now rather than
      // waiting for the 2 s workspace poll to notice.
      selectCycle(sel.campaignId, sel.cycleId);
    },
  });
  const startNew = useCallback(() => {
    setComposing(true);
    flow.reset();
    // `flow` is rebuilt each render but its methods close over stable setState,
    // so the identity churn is not a correctness problem — the exhaustive-deps
    // lint would over-add it.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const value = useMemo(
    () => ({ flow, collection, composing, startNew, mintCount }),
    [flow, collection, composing, startNew, mintCount],
  );
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useIngest(): IngestFlowValue {
  const v = useContext(Ctx);
  if (!v) throw new Error("useIngest must be used inside IngestFlowProvider");
  return v;
}
