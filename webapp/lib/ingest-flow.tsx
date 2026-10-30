"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  type ReactNode,
} from "react";
import {
  NEW_CHECKIN,
  useIngestFlow,
  type CheckinSubject,
  type IngestFlow,
} from "@/lib/hooks/useIngestFlow";
import { useWorkspace } from "@/lib/workspace";

// ONE instance app-wide: a second `useIngestFlow` mints a second live server-side draft.
interface IngestFlowValue {
  flow: IngestFlow;
  // The viewed check-in: a draft id, `NEW_CHECKIN` before one is minted, null while a run is viewed.
  subject: CheckinSubject;
  // `+ New campaign`: a fresh check-in's address.
  compose: () => void;
  // An unstarted campaign's address: the flow reads its draft in.
  openDraft: (draftId: string) => void;
}

const IngestFlowContext = createContext<IngestFlowValue | null>(null);

export function IngestFlowProvider({ children }: { children: ReactNode }) {
  const { checkin, viewedPath, navigate, openCheckin } = useWorkspace();
  // Nothing viewed at all (no run yet, or none followed) is a fresh check-in too.
  const subject: CheckinSubject =
    checkin !== null ? (checkin.draftId ?? NEW_CHECKIN) : viewedPath === null ? NEW_CHECKIN : null;
  // Read by the flow's continuations, which outlive the render that started them.
  const subjectRef = useRef(subject);
  useEffect(() => {
    subjectRef.current = subject;
  });
  const flow = useIngestFlow({
    subject,
    onMint: (sel) => navigate([{ campaignId: sel.campaignId, cycleId: sel.cycleId }]),
    // Only a fresh check-in still on screen follows its draft: a run viewed meanwhile stays viewed.
    onDraft: (draftId) => {
      if (subjectRef.current === NEW_CHECKIN) openCheckin(draftId);
    },
    onDiscarded: (draftId) => {
      if (subjectRef.current === draftId) openCheckin(null);
    },
    onCompose: () => openCheckin(null),
  });

  const compose = useCallback(() => openCheckin(null), [openCheckin]);
  const openDraft = useCallback((draftId: string) => openCheckin(draftId), [openCheckin]);

  const value = useMemo(
    () => ({ flow, subject, compose, openDraft }),
    [flow, subject, compose, openDraft],
  );
  return <IngestFlowContext.Provider value={value}>{children}</IngestFlowContext.Provider>;
}

export function useIngest(): IngestFlowValue {
  const v = useContext(IngestFlowContext);
  if (!v) throw new Error("useIngest must be used inside IngestFlowProvider");
  return v;
}
