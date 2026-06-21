"use client";
// IngestPane — the "New campaign" modal: a thin shell over the unified ingest
// conversation. One surface, one path: the entry list shows the operator's
// registered datasets + benchmarks alongside a drop input; picking a dataset or
// dropping a file runs the same draft → (context if missing) → one check-in →
// Start flow as the dashboard chat tab (`IngestConversation` + `useIngestFlow`
// are shared by both).
//
// Wire contract: `docs/specs/m12-api-openapi.yaml`
//   POST /datasets/ingest                       (upload → DraftCampaign)
//   POST /datasets/{name}/draft                 (existing dataset → DraftCampaign)
//   POST /commands/edit-draft-campaign          (sparse-patch)
//   POST /commands/resolve-origin               (the one check-in call)
//   POST /commands/start-checkin                (gate + commit + spawn runner)

import { useEffect, useState } from "react";
import {
  fetchDatasetIndex,
  fetchOrigins,
  type DatasetIndexEntry,
  type OriginEntry,
} from "@/lib/api";
import { useIngestFlow } from "@/lib/hooks/useIngestFlow";
import { IngestConversation } from "./IngestConversation";
import { useAuth } from "@/lib/auth-context";
import { useDialogA11y } from "@/lib/hooks/useDialogA11y";
import { SignInPrompt } from "@/components/ui";
import type { OnMinted } from "./types";

interface Props {
  open: boolean;
  onClose: () => void;
  onMinted?: OnMinted;
}

type LoadState =
  | { kind: "loading" }
  | { kind: "needsAuth" }
  | { kind: "ready"; origins: OriginEntry[]; entries: DatasetIndexEntry[] }
  | { kind: "error" };

export function IngestPane({ open, onClose, onMinted }: Props) {
  const { status } = useAuth();
  const [prevOpen, setPrevOpen] = useState(open);
  const [list, setList] = useState<LoadState>({ kind: "loading" });

  // The unified state machine. On mint, select the new cycle (via onMinted) and
  // close the modal.
  const flow = useIngestFlow({
    onMint: (sel) => {
      onMinted?.(sel);
      onClose();
    },
  });

  // The list's pre-fetch resting state, chosen by auth: a confirmed (or still
  // resolving) session shows loading while the effect below fetches; an anon
  // visitor shows the needs-auth prompt and the effect fires nothing
  // (frontend-surface-contract.md § I1/I5).
  const gatedInitial = (): LoadState =>
    status === "authed" || status === "loading"
      ? { kind: "loading" }
      : { kind: "needsAuth" };

  // Render-phase guarded reset on `open` toggles — clears the conversation and
  // re-arms the list. Runs during render so the reset commits with the open
  // frame (webapp/CLAUDE.md "State reset on prop change").
  if (open !== prevOpen) {
    setPrevOpen(open);
    if (open) {
      setList(gatedInitial());
      flow.reset();
    }
  }
  // Re-resolve the resting state when auth settles while the modal is open.
  const [prevStatus, setPrevStatus] = useState(status);
  if (status !== prevStatus) {
    setPrevStatus(status);
    if (open) setList(gatedInitial());
  }
  const cardRef = useDialogA11y(open, onClose);

  // Gated on a confirmed session: anon never fires the protected `/datasets`
  // read (it would 401 and render raw; frontend-surface-contract.md § I1/I5).
  useEffect(() => {
    if (!open || status !== "authed") return;
    let cancelled = false;
    Promise.all([fetchDatasetIndex(), fetchOrigins()])
      .then(([datasets, origins]) => {
        if (!cancelled)
          setList({ kind: "ready", origins: origins.origins, entries: datasets.datasets });
      })
      .catch(() => {
        if (!cancelled) setList({ kind: "error" });
      });
    return () => {
      cancelled = true;
    };
  }, [open, status]);

  if (!open) return null;

  const body =
    list.kind === "ready" ? (
      <IngestConversation
        flow={flow}
        origins={list.origins}
        datasets={list.entries}
        variant="modal"
      />
    ) : list.kind === "needsAuth" ? (
      <div className="new-campaign-body">
        <SignInPrompt message="Sign in to start a campaign." />
      </div>
    ) : list.kind === "error" ? (
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
