"use client";
// IngestPane — the "New campaign" modal, and only the DOOR to a campaign: it
// shows the resting entry list (registered datasets + benchmarks, plus a drop
// input) for the tabs that are not Chat. Picking or dropping advances the one
// shared thread (`lib/ingest-flow.tsx`), at which point the shell closes this
// and moves to the chat tab, where the draft → (context if missing) → one
// check-in → Start conversation actually happens. It holds no flow of its own —
// that is what let a modal opened over the chat tab carry a second live draft.
//
// Wire contract: `docs/specs/api-openapi.yaml`
//   POST /datasets/ingest                       (upload → DraftCampaign)
//   POST /datasets/{name}/draft                 (existing dataset → DraftCampaign)
//   POST /commands/edit-draft-campaign          (sparse-patch)
//   POST /commands/resolve-origin               (the one check-in call)
//   POST /commands/start-checkin                (gate + commit + spawn runner)

import { useState } from "react";
import { IngestConversation } from "./IngestConversation";
import { useIngest } from "@/lib/ingest-flow";
import { useDialogA11y } from "@/lib/hooks/useDialogA11y";
import { SignInPrompt } from "@/components/ui";

interface Props {
  open: boolean;
  onClose: () => void;
}

export function IngestPane({ open, onClose }: Props) {
  // The shared thread and the shared collection — this modal owns neither. It is
  // the entry door for the tabs that are not Chat; the shell closes it and moves
  // to the chat tab as soon as a pick or a drop advances the flow, so the
  // conversation itself only ever happens in one place.
  const { flow, collection, startNew } = useIngest();
  const [prevOpen, setPrevOpen] = useState(open);

  // Render-phase guarded reset on the open edge — a fresh gesture starts a fresh
  // thread. Runs during render so the reset commits with the open frame
  // (webapp/CLAUDE.md "State reset on prop change").
  if (open !== prevOpen) {
    setPrevOpen(open);
    if (open) startNew();
  }
  const cardRef = useDialogA11y(open, onClose);

  if (!open) return null;

  const body =
    collection.kind === "ready" ? (
      <IngestConversation
        flow={flow}
        origins={collection.origins}
        datasets={collection.entries}
      />
    ) : collection.kind === "needsAuth" ? (
      <div className="new-campaign-body">
        <SignInPrompt message="Sign in to start a campaign." />
      </div>
    ) : collection.kind === "error" ? (
      <div className="new-campaign-body">
        <p className="new-campaign-error">Couldn’t load your collection — retry shortly.</p>
      </div>
    ) : (
      <div className="new-campaign-body">
        <em className="new-campaign-loading">Loading your collection…</em>
      </div>
    );

  return (
    <div
      className="new-campaign-overlay"
      role="dialog"
      aria-modal="true"
      aria-label="New campaign"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div ref={cardRef} className="new-campaign-modal">
        <header className="new-campaign-header">
          <h2>{flow.phase.stage === "ready" ? "Set up campaign" : "New campaign"}</h2>
          <button type="button" className="new-campaign-close" aria-label="Close" onClick={onClose}>
            ×
          </button>
        </header>
        {body}
      </div>
    </div>
  );
}
