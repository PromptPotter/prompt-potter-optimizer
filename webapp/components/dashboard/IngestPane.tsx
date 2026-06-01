"use client";
// IngestPane — the M13 chat-first "New campaign" surface.
//
// Two-mode entry against the identity-scoped `GET /datasets`:
//   * empty `tier: "yours"` → drop a CSV, see preview, tune defaults, commit
//   * non-empty → list the collection, pick an Origin, mint a campaign
//
// Wire contract: `docs/specs/m12-api-openapi.yaml`
//   POST /datasets/ingest                       (upload → DraftCampaign)
//   POST /commands/edit-draft-campaign          (sparse-patch via panel/chat)
//   POST /commands/mint-campaign-from-draft     (commit + spawn runner)
//   POST /commands/mint-campaign                (existing Origin → runner)
//
// Per `docs/specs/m13-chat-first-user-web.md § Draft-campaign object`, the
// chat surface and the panel are two views over the same server-held
// `DraftCampaign`. Slice 1 ships the panel-side; the chat tool-call surface
// reuses the same `postEditDraftCampaign` verb.

import { useEffect, useState } from "react";
import {
  fetchDatasetIndex,
  IngestApiError,
  postDraftFromDataset,
  postIngestDataset,
  type DatasetIndexEntry,
  type DraftCampaignWire,
} from "@/lib/api";
import { ChatIngestFlow } from "./ingest/ChatIngestFlow";
import { ListAndMintFlow } from "./ingest/ListAndMintFlow";
import { DraftCommitFlow } from "./ingest/DraftCommitFlow";
import type { OnMinted } from "./ingest/types";

interface Props {
  open: boolean;
  onClose: () => void;
  onMinted?: OnMinted;
}

type LoadState =
  | { kind: "loading" }
  | { kind: "ready"; entries: DatasetIndexEntry[] }
  | { kind: "error"; message: string };

export function IngestPane({ open, onClose, onMinted }: Props) {
  // Render-phase guarded reset on `open` toggles. Async fetch fires from a
  // bare effect that never synchronously setStates.
  const [prevOpen, setPrevOpen] = useState(open);
  const [list, setList] = useState<LoadState>({ kind: "loading" });
  // Setup-flow state lives here, not in the per-screen children: a CSV drop OR
  // a demo pick (from EITHER screen) produces a draft, and once a draft exists
  // the commit wizard renders regardless of which screen launched it. No screen
  // instant-mints a demo — picking one always lands in the wizard, prefilled.
  const [draft, setDraft] = useState<DraftCampaignWire | null>(null);
  const [flowBusy, setFlowBusy] = useState(false);
  const [flowError, setFlowError] = useState<string | null>(null);
  if (open !== prevOpen) {
    setPrevOpen(open);
    if (open) {
      setList({ kind: "loading" });
      setDraft(null);
      setFlowError(null);
    }
  }

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    fetchDatasetIndex()
      .then((r) => {
        if (!cancelled) setList({ kind: "ready", entries: r.datasets });
      })
      .catch((e) => {
        if (!cancelled) {
          setList({
            kind: "error",
            message: e instanceof Error ? e.message : String(e),
          });
        }
      });
    return () => {
      cancelled = true;
    };
  }, [open]);

  if (!open) return null;

  const ingestFile = async (file: File) => {
    setFlowError(null);
    setFlowBusy(true);
    try {
      setDraft(await postIngestDataset(file));
    } catch (e) {
      setFlowError(IngestApiError.toOperatorMessage(e));
    } finally {
      setFlowBusy(false);
    }
  };

  // A demo is just a dataset: opening one asks the server to build a prefilled
  // draft straight from the dataset's files (columns, task, scoring, and the
  // backend model config all carried), then hands off to the same setup wizard
  // a dropped CSV uses. Never an instant mint, no browser-side CSV round-trip.
  const pickDemo = async (entry: DatasetIndexEntry) => {
    setFlowError(null);
    setFlowBusy(true);
    try {
      setDraft(await postDraftFromDataset(entry.name));
    } catch (e) {
      setFlowError(IngestApiError.toOperatorMessage(e));
    } finally {
      setFlowBusy(false);
    }
  };

  const ownedEntries =
    list.kind === "ready" ? list.entries.filter((d) => d.tier === "yours") : [];
  // The try-and-learn demo rides the same "ready-to-run" bucket as benchmarks.
  const benchmarkEntries =
    list.kind === "ready"
      ? list.entries.filter((d) => d.tier === "benchmark" || d.tier === "demo")
      : [];

  const body =
    draft !== null ? (
      <DraftCommitFlow
        draft={draft}
        onDraftChange={setDraft}
        onClose={onClose}
        onMinted={onMinted}
      />
    ) : list.kind === "loading" ? (
      <div className="new-campaign-body">
        <em className="new-campaign-loading">Loading your collection…</em>
      </div>
    ) : list.kind === "error" ? (
      <div className="new-campaign-body">
        <p className="new-campaign-error">{list.message}</p>
      </div>
    ) : ownedEntries.length === 0 ? (
      <ChatIngestFlow
        benchmarks={benchmarkEntries}
        busy={flowBusy}
        uploadError={flowError}
        onFile={ingestFile}
        onPickDemo={pickDemo}
        onClose={onClose}
        onMinted={onMinted}
      />
    ) : (
      <ListAndMintFlow
        owned={ownedEntries}
        benchmarks={benchmarkEntries}
        busy={flowBusy}
        onPickDemo={pickDemo}
        onClose={onClose}
        onMinted={onMinted}
        onAddOrigin={() => setList({ kind: "ready", entries: benchmarkEntries })}
      />
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
      <div className="new-campaign-modal">
        <header className="new-campaign-header">
          <h2>New campaign</h2>
          <button
            type="button"
            className="new-campaign-close"
            aria-label="Close"
            onClick={onClose}
          >
            ×
          </button>
        </header>
        {body}
      </div>
    </div>
  );
}
