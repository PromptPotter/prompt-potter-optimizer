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
  type OriginLastResolution,
} from "@/lib/api";
import { ChatIngestFlow } from "./ChatIngestFlow";
import { ListAndMintFlow } from "./ListAndMintFlow";
import { DraftCommitFlow, type WizardStep } from "./DraftCommitFlow";
import { CheckinLoadingWindow } from "./CheckinLoadingWindow";
import { useAuth } from "@/lib/auth-context";
import { useDialogA11y } from "@/lib/hooks/useDialogA11y";
import { SignInPrompt } from "@/components/ui/states";
import type { OnMinted } from "./types";

// How long the demo's simulated check-in window lingers before the prefilled
// wizard lands — long enough to read the model + see the counter tick, short
// enough not to stall. The real flow shows the window for the actual call.
const DEMO_CHECKIN_MS = 2200;

// Per-step modal titles — the 1-2-3 launch the operator walks. Pre-draft (the
// drop / collection screen) keeps the plain title.
const STEP_TITLES: Record<WizardStep, string> = {
  1: "Step 1 of 3 · Your data & goal",
  2: "Step 2 of 3 · Map columns",
  3: "Step 3 of 3 · Pipeline & prompt",
};

interface Props {
  open: boolean;
  // A draft pre-resolved elsewhere (the chat file-drop check-in) — when set, the
  // wizard opens straight on it instead of the empty drop screen.
  initialDraft?: DraftCampaignWire | null;
  // The chat check-in's resolution, seeded into the wizard's check-in panel so
  // its assessment/questions show immediately (no "Set up with AI" re-click).
  initialResolution?: OriginLastResolution | null;
  onClose: () => void;
  onMinted?: OnMinted;
}

type LoadState =
  | { kind: "loading" }
  | { kind: "needsAuth" }
  | { kind: "ready"; entries: DatasetIndexEntry[] }
  | { kind: "error" };

export function IngestPane({
  open,
  initialDraft,
  initialResolution,
  onClose,
  onMinted,
}: Props) {
  const { status } = useAuth();
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
  // Wizard step, owned here so the modal header can title each step. Resets to
  // 1 whenever the active draft changes (a new drop / demo pick).
  const [step, setStep] = useState<WizardStep>(1);
  const [prevDraftId, setPrevDraftId] = useState<string | null>(null);
  // Non-null while the demo's simulated check-in window is showing — carries the
  // model name to display. Cleared when the prefilled wizard lands.
  const [checkinModel, setCheckinModel] = useState<string | null>(null);
  // The list's pre-fetch resting state, chosen by auth: a confirmed (or still
  // resolving) session shows loading while the effect below fetches; an anon
  // visitor shows the needs-auth prompt and the effect fires nothing
  // (frontend-surface-contract.md § I1/I5).
  const gatedInitial = (): LoadState =>
    status === "authed" || status === "loading"
      ? { kind: "loading" }
      : { kind: "needsAuth" };
  if (open !== prevOpen) {
    setPrevOpen(open);
    if (open) {
      setList(gatedInitial());
      // Seed a chat-resolved draft so the wizard opens straight on it; a plain
      // "+ New" passes none and lands on the empty drop screen as before.
      setDraft(initialDraft ?? null);
      setFlowError(null);
      setCheckinModel(null);
    }
  }
  // Re-resolve the resting state when auth settles while the modal is
  // open (loading→authed kicks the fetch; →unauthed shows the prompt).
  const [prevStatus, setPrevStatus] = useState(status);
  if (status !== prevStatus) {
    setPrevStatus(status);
    if (open) setList(gatedInitial());
  }
  const draftId = draft?.draft_id ?? null;
  if (draftId !== prevDraftId) {
    setPrevDraftId(draftId);
    setStep(1);
  }
  // ESC + focus-trap + focus-restore from the shared hook; this modal keeps its
  // own .new-campaign-modal wizard layout rather than Dialog's confirm-card.
  const cardRef = useDialogA11y(open, onClose);

  // Hand-rolled, not useFetch: `list` is mutable view state — onAddOrigin below
  // imperatively flips it back to the picker — not a read-only fetch result.
  // Gated on a confirmed session: anon never fires the protected `/datasets`
  // read (it would 401 and render raw; frontend-surface-contract.md § I1/I5).
  useEffect(() => {
    if (!open || status !== "authed") return;
    let cancelled = false;
    fetchDatasetIndex()
      .then((r) => {
        if (!cancelled) setList({ kind: "ready", entries: r.datasets });
      })
      .catch(() => {
        if (!cancelled) setList({ kind: "error" });
      });
    return () => {
      cancelled = true;
    };
  }, [open, status]);

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
      // The demo simulates the check-in: build the prefilled draft, then show
      // its check-in window (model + counter) for a beat before landing the
      // wizard — the same window the real resolve call shows.
      const d = await postDraftFromDataset(entry.name);
      setCheckinModel(d.optimizer_model || "the check-in model");
      await new Promise((resolve) => setTimeout(resolve, DEMO_CHECKIN_MS));
      setDraft(d);
    } catch (e) {
      setFlowError(IngestApiError.toOperatorMessage(e));
    } finally {
      setFlowBusy(false);
      setCheckinModel(null);
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
    checkinModel !== null ? (
      <div className="new-campaign-body">
        <CheckinLoadingWindow model={checkinModel} />
      </div>
    ) : draft !== null ? (
      <DraftCommitFlow
        draft={draft}
        step={step}
        onStep={setStep}
        onDraftChange={setDraft}
        initialResolution={initialResolution}
        onClose={onClose}
        onMinted={onMinted}
      />
    ) : list.kind === "loading" ? (
      <div className="new-campaign-body">
        <em className="new-campaign-loading">Loading your collection…</em>
      </div>
    ) : list.kind === "needsAuth" ? (
      <div className="new-campaign-body">
        <SignInPrompt message="Sign in to start a campaign." />
      </div>
    ) : list.kind === "error" ? (
      <div className="new-campaign-body">
        <p className="new-campaign-error">
          Couldn’t load your collection — retry shortly.
        </p>
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
      <div ref={cardRef} className="new-campaign-modal">
        <header className="new-campaign-header">
          <h2>
            {checkinModel !== null
              ? "Check-in"
              : draft !== null
                ? STEP_TITLES[step]
                : "New campaign"}
          </h2>
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
