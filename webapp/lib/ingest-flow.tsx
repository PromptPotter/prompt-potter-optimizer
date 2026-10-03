"use client";

import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from "react";
import { useIngestFlow, type IngestFlow } from "@/lib/hooks/useIngestFlow";
import { useCollection, type CollectionState } from "@/lib/hooks/useCollection";
import { useWorkspace } from "@/lib/workspace";

// ONE authoring thread for the whole app: a draft is a fact about the operator, not the pane, and
// a second `useIngestFlow` instance mints a second live server-side draft.
interface IngestFlowValue {
  flow: IngestFlow;
  collection: CollectionState;
  // Suppresses the bound cycle's live feed and run card, so a fresh thread is not rendered over
  // the previous campaign's activity. Cleared on mint.
  composing: boolean;
  startNew: () => void;
  // A host with its own landing side effect guards on this rather than owning a second mint callback.
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
      selectCycle(sel.campaignId, sel.cycleId);
    },
  });
  const startNew = useCallback(() => {
    setComposing(true);
    flow.reset();
    // `flow` is rebuilt each render, but its methods close over stable setState.
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
