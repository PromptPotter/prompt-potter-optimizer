"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { useIngestFlow, type IngestFlow } from "@/lib/hooks/useIngestFlow";
import { useCollection, type CollectionState } from "@/lib/hooks/useCollection";
import { useWorkspace } from "@/lib/workspace";

// ONE instance app-wide: a second `useIngestFlow` mints a second live server-side draft.
interface IngestFlowValue {
  flow: IngestFlow;
  collection: CollectionState;
  // Suppresses the bound cycle's live feed and run card under a fresh thread.
  composing: boolean;
  startNew: () => void;
  composerOpen: boolean;
  openComposer: () => void;
  closeComposer: () => void;
  compose: () => void;
}

const Ctx = createContext<IngestFlowValue | null>(null);

export function IngestFlowProvider({ children }: { children: ReactNode }) {
  const [composing, setComposing] = useState(false);
  const [composerOpen, setComposerOpen] = useState(false);
  const collection = useCollection();
  const { tab, navigate, openView } = useWorkspace();
  const flow = useIngestFlow({
    onMint: (sel) => {
      setComposing(false);
      navigate([{ campaignId: sel.campaignId, cycleId: sel.cycleId }]);
    },
  });
  const startNew = useCallback(() => {
    setComposing(true);
    flow.reset();
    // `flow` is rebuilt each render, but its methods close over stable setState.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  const openComposer = useCallback(() => setComposerOpen(true), []);
  const closeComposer = useCallback(() => setComposerOpen(false), []);
  const compose = useCallback(() => {
    if (tab === "chat") startNew();
    else setComposerOpen(true);
    // Re-opening the view on screen only leaves the phone's list.
    openView(tab);
  }, [tab, startNew, openView]);

  // The modal closes in render phase, so it never paints over the thread it hands over to.
  const stage = flow.phase.stage;
  const [prevStage, setPrevStage] = useState(stage);
  const [handovers, setHandovers] = useState(0);
  if (stage !== prevStage) {
    setPrevStage(stage);
    if (composerOpen && stage !== "idle") {
      setComposerOpen(false);
      setHandovers((n) => n + 1);
    }
  }
  useEffect(() => {
    if (handovers > 0) openView("chat");
  }, [handovers, openView]);

  const value = useMemo(
    () => ({
      flow,
      collection,
      composing,
      startNew,
      composerOpen,
      openComposer,
      closeComposer,
      compose,
    }),
    [flow, collection, composing, startNew, composerOpen, openComposer, closeComposer, compose],
  );
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useIngest(): IngestFlowValue {
  const v = useContext(Ctx);
  if (!v) throw new Error("useIngest must be used inside IngestFlowProvider");
  return v;
}
