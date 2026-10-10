"use client";
// Only the DOOR to the shared thread: a flow of its own here is a second draft.

import { IngestComposer, useIngestItems } from "./IngestConversation";
import { useIngest } from "@/lib/ingest-flow";
import { useThread } from "@/lib/chat/thread";
import type { RunSummary } from "@/lib/derivations";
import { Thread } from "@/components/chat/Thread";
import { RunSummaryItem } from "@/components/chat/RunCard";
import { Button, Dialog, IconClose, SignInPrompt } from "@/components/ui";

export function IngestPane() {
  const { flow, collection, closeComposer: onClose } = useIngest();
  const thread = useThread<RunSummary>();
  const ingestItems = useIngestItems(thread.items.length === 0);

  const body =
    collection.kind === "ready" ? (
      <div className="ingest-conversation">
        <Thread
          items={[...ingestItems.head, ...thread.items, ...ingestItems.tail]}
          renderRun={(summary) => <RunSummaryItem summary={summary} />}
        />
        <IngestComposer />
      </div>
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
