"use client";

import { useEffect } from "react";
import { useIngestFlow } from "@/lib/hooks/useIngestFlow";
import { IngestConversation } from "./IngestConversation";
import type { OnMinted } from "./types";

// Re-open surface for a durable `checkin`-lifecycle campaign selected from the
// sidebar. A check-in hasn't run, so it has no `dashboard.json` — opening it on
// the dashboard would warm forever (a dead-end). Instead we rebuild the ingest
// "ready" panel from `GET /campaigns/{id}/checkin` so the operator finishes
// authoring the origin and Starts. The campaign survives a server restart; this
// re-open is the payoff for making check-in durable.
export function CheckinReopenPane({
  campaignId,
  onStarted,
}: {
  campaignId: string;
  onStarted: OnMinted;
}) {
  const flow = useIngestFlow({ onMint: onStarted });
  // Load (and reload when the selected check-in changes). `flow` is recreated each
  // render but its methods close over stable setState, so keying on campaignId is
  // the correct trigger — the exhaustive-deps lint would over-add `flow`.
  useEffect(() => {
    flow.reopenCheckin(campaignId);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [campaignId]);

  return (
    <div className="content">
      <IngestConversation flow={flow} variant="modal" />
    </div>
  );
}
