"use client";
// The "New campaign" modal — only the DOOR: a pick or drop advances the shared thread and the
// shell moves to the chat tab. It must hold no flow of its own, or it carries a second draft.

import { useState } from "react";
import { IngestConversation } from "./IngestConversation";
import { useIngest } from "@/lib/ingest-flow";
import { Button, Dialog, IconClose, SignInPrompt } from "@/components/ui";

interface Props {
  open: boolean;
  onClose: () => void;
}

export function IngestPane({ open, onClose }: Props) {
  const { flow, collection, startNew } = useIngest();
  const [prevOpen, setPrevOpen] = useState(open);

  // A fresh open starts a fresh thread.
  if (open !== prevOpen) {
    setPrevOpen(open);
    if (open) startNew();
  }

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
    <Dialog open onClose={onClose} labelledBy="new-campaign-title" bare>
      <div className="new-campaign-modal">
        <header className="new-campaign-header">
          <h2 id="new-campaign-title">
            {flow.phase.stage === "ready" ? "Set up campaign" : "New campaign"}
          </h2>
          <Button variant="ghost" aria-label="Close" onClick={onClose}>
            <IconClose />
          </Button>
        </header>
        {body}
      </div>
    </Dialog>
  );
}
