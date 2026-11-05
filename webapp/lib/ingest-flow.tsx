import { createContext, useContext, useEffect, useRef, type ReactNode } from "react";
import {
  NEW_CHECKIN,
  useIngestFlow,
  type CheckinSubject,
  type IngestFlow,
} from "@/lib/hooks/useIngestFlow";
import type { DraftCampaignWire } from "@/lib/api/types";
import { useWorkspace } from "@/lib/workspace";

// The viewed check-in: a draft id, `NEW_CHECKIN` before one is minted, null while a run is viewed.
// Nothing viewed at all (no run yet, or none followed) is a fresh check-in too.
export function useCheckinSubject(): CheckinSubject {
  const { checkin, viewedPath } = useWorkspace();
  return checkin !== null
    ? (checkin.draftId ?? NEW_CHECKIN)
    : viewedPath === null
      ? NEW_CHECKIN
      : null;
}

// ONE instance app-wide: a second `useIngestFlow` mints a second live server-side draft.
const IngestFlowContext = createContext<IngestFlow | null>(null);

export function IngestFlowProvider({ children }: { children: ReactNode }) {
  const { navigate } = useWorkspace();
  const subject = useCheckinSubject();
  // Read by the flow's continuations, which outlive the render that started them.
  const subjectRef = useRef(subject);
  useEffect(() => {
    subjectRef.current = subject;
  });
  const flow = useIngestFlow({
    subject,
    onMint: (sel) => navigate({ path: [{ campaignId: sel.campaignId, cycleId: sel.cycleId }] }),
    // Only a fresh check-in still on screen follows its draft: a run viewed meanwhile stays viewed.
    onDraft: (draftId) => {
      if (subjectRef.current === NEW_CHECKIN) navigate({ draftId });
    },
    onDiscarded: (draftId) => {
      if (subjectRef.current === draftId) navigate({ draftId: null });
    },
    onCompose: () => navigate({ draftId: null }),
  });
  return <IngestFlowContext.Provider value={flow}>{children}</IngestFlowContext.Provider>;
}

export function useIngest(): IngestFlow {
  const v = useContext(IngestFlowContext);
  if (!v) throw new Error("useIngest must be used inside IngestFlowProvider");
  return v;
}

// The viewed check-in's draft once it is ready to author; null off one.
export function useCheckinDraft(): DraftCampaignWire | null {
  const flow = useIngest();
  const subject = useCheckinSubject();
  return subject !== null && flow.phase.stage === "ready" ? flow.phase.draft : null;
}
