"use client";

import { useRef, useState } from "react";
import {
  failureKind,
  IngestApiError,
  operatorMessage,
  postDraftFromDataset,
  postDraftFromOrigin,
  getCampaignCheckin,
  postEditDraftCampaign,
  postIngestDataset,
  postStartCheckin,
  postBuildCandidateLibraryFromColumn,
  postReplaceDataset,
  postResolveOrigin,
  postUploadCandidateLibrary,
  type DatasetIndexEntry,
  type DraftCampaignWire,
  type DraftPatch,
  type OriginEntry,
  type OriginLastResolution,
  type RaisedCommand,
  type StartCheckinLimits,
} from "@/lib/api";
import { plainLanguageRecap } from "@/lib/origin-readiness";
import type { RunSummary } from "@/lib/derivations";
import type { OnMinted } from "@/components/ingest/types";

type ChatMsg =
  | { id: string; kind: "user-file"; name: string; rows: number | null }
  | { id: string; kind: "user"; text: string }
  | { id: string; kind: "ai"; text: string }
  | { id: string; kind: "warning"; text: string }
  | { id: string; kind: "error"; text: string }
  // Captured VALUES, never a pointer into `dashboard.json`: `resume` re-animates that file, so a
  // pointer would restate itself as the next run.
  | { id: string; kind: "run"; summary: RunSummary };

type IngestPhase =
  | { stage: "idle" }
  | { stage: "uploading" }
  | { stage: "awaiting-context"; draft: DraftCampaignWire }
  | { stage: "checkin"; model: string }
  | {
      stage: "collision";
      file: File;
      chipId: string;
      existingSlug: string;
      suggestedSlug: string;
    }
  | {
      stage: "ready";
      draft: DraftCampaignWire;
      resolution: OriginLastResolution | null;
      // Offered, never fired: only the operator's click sends `edit-draft-campaign`.
      raised: RaisedCommand[];
      degradedCause: string | null;
    };

export interface IngestFlow {
  messages: ChatMsg[];
  phase: IngestPhase;
  inputText: string;
  setInputText: (v: string) => void;
  busy: boolean;
  // NOT folded into `busy` and disables nothing: disabling Start here eats the tap whose
  // mousedown blurred the field; `startFromReady` awaits the in-flight writes instead.
  saving: boolean;
  awaitingContext: boolean;
  onDatasetFile: (file: File) => void;
  pickDataset: (entry: DatasetIndexEntry) => void;
  // No check-in call: the optimizer graph enters at l1_generate.
  openOrigin: (entry: OriginEntry) => void;
  reopenCheckin: (campaignId: string) => void;
  submitContext: () => void;
  applyPatch: (patch: DraftPatch) => void;
  uploadCandidateLibrary: (file: File) => void;
  buildCandidateLibraryFromColumn: (column: string) => void;
  rerunCheckin: () => void;
  // The limits are the caller's, not draft state: nothing persists them, so a reopened check-in
  // must not appear to hold a budget nobody re-entered.
  startFromReady: (limits: StartCheckinLimits) => void;
  useExistingFromCollision: () => void;
  saveAsNew: () => void;
  replaceExisting: () => void;
  cancelCollision: () => void;
  pushRunSummary: (summary: RunSummary) => void;
  reset: () => void;
}

const uid = () => crypto.randomUUID();

// The single dataset → origin → campaign state machine. Instantiate it ONCE, in
// `lib/ingest-flow.tsx`; every surface reads that provider.
export function useIngestFlow({ onMint }: { onMint: OnMinted }): IngestFlow {
  const [messages, setMessages] = useState<ChatMsg[]>([]);
  const [phase, setPhase] = useState<IngestPhase>({ stage: "idle" });
  const [inputText, setInputText] = useState("");
  const [minting, setMinting] = useState(false);
  // Counter, not a boolean: overlapping patches are the designed case, so the first to land
  // must not clear the flag under a sibling.
  const [pendingPatches, setPendingPatches] = useState(0);
  const pendingDraftWrites = useRef<Set<Promise<unknown>>>(new Set());

  const busy =
    phase.stage === "uploading" || phase.stage === "checkin" || minting;
  const awaitingContext = phase.stage === "awaiting-context";

  const pushAi = (text: string) =>
    setMessages((m) => [...m, { id: uid(), kind: "ai", text }]);
  const pushWarning = (text: string) =>
    setMessages((m) => [...m, { id: uid(), kind: "warning", text }]);

  // Functional update, so overlapping patches cannot write a stale snapshot back.
  const commitDraftUpdate = (updated: DraftCampaignWire) =>
    setPhase((p) => (p.stage === "ready" ? { ...p, draft: updated } : p));
  const pushError = (e: unknown) =>
    setMessages((m) => [
      ...m,
      { id: uid(), kind: "error", text: operatorMessage(e, failureKind(e)) },
    ]);

  const runCheckin = async (draft: DraftCampaignWire) => {
    setPhase({ stage: "checkin", model: "the check-in model" });
    let resolved = draft;
    let resolution: OriginLastResolution | null = null;
    let raised: RaisedCommand[] = [];
    let degradedCause: string | null = null;
    let recap = "";
    try {
      const r = await postResolveOrigin(draft.draft_id);
      resolved = r.draft;
      resolution = r.resolution.last_resolution ?? null;
      raised = r.resolution.raised ?? [];
      degradedCause = r.resolution.degraded_cause ?? null;
      recap = resolution?.recap || resolution?.assessment || plainLanguageRecap(resolved);
    } catch (e) {
      recap = plainLanguageRecap(resolved);
      pushError(e);
    }
    if (degradedCause) pushWarning(degradedCause);
    pushAi(recap);
    // Even a complete draft lands in review: a launch spends money, so no path here mints.
    setPhase({ stage: "ready", draft: resolved, resolution, raised, degradedCause });
  };

  const advance = (draft: DraftCampaignWire) => {
    if (draft.raw_task_description.trim()) {
      setInputText("");
      pushAi(`Parsed ${draft.n_samples} rows — task already on file. Checking the setup…`);
      void runCheckin(draft);
      return;
    }
    setInputText("");
    pushAi(
      `Parsed ${draft.n_samples} rows. Describe the task — what should the model do with each row? The more you give me, the better I set up the prompt and pipeline. Send when ready.`,
    );
    setPhase({ stage: "awaiting-context", draft });
  };

  const ingestAndResolve = async (file: File, slug?: string, chipId?: string) => {
    // A refused drop must SAY so, or the previous campaign's draft reads as this file's.
    if (busy) {
      pushWarning(
        `“${file.name}” wasn’t picked up — the previous step was still loading. ` +
          `Nothing on screen is from this file; drop it again.`,
      );
      return;
    }
    const id = chipId ?? uid();
    if (!chipId) {
      setMessages((m) => [...m, { id, kind: "user-file", name: file.name, rows: null }]);
    }
    setPhase({ stage: "uploading" });
    let draft: DraftCampaignWire;
    try {
      draft = await postIngestDataset(file, slug);
    } catch (e) {
      if (
        e instanceof IngestApiError &&
        e.status === 409 &&
        e.existingSlug &&
        e.suggestedSlug
      ) {
        setPhase({
          stage: "collision",
          file,
          chipId: id,
          existingSlug: e.existingSlug,
          suggestedSlug: e.suggestedSlug,
        });
        return;
      }
      setPhase({ stage: "idle" });
      pushError(e);
      return;
    }
    setMessages((m) =>
      m.map((msg) =>
        msg.id === id && msg.kind === "user-file" ? { ...msg, rows: draft.n_samples } : msg,
      ),
    );
    advance(draft);
  };

  const draftFrom = async (name: string, label: string) => {
    if (busy) return;
    setMessages((m) => [...m, { id: uid(), kind: "user", text: label }]);
    setPhase({ stage: "uploading" });
    let draft: DraftCampaignWire;
    try {
      draft = await postDraftFromDataset(name);
    } catch (e) {
      setPhase({ stage: "idle" });
      pushError(e);
      return;
    }
    advance(draft);
  };

  const pickDataset = (entry: DatasetIndexEntry) =>
    void draftFrom(entry.name, `Use dataset “${entry.title || entry.name}”`);

  // A prepared origin IS its dataset's current config, so the dataset-draft path reproduces it.
  const openOrigin = async (entry: OriginEntry) => {
    if (busy) return;
    setMessages((m) => [
      ...m,
      { id: uid(), kind: "user", text: `Reuse origin “${entry.label || entry.dataset_name}”` },
    ]);
    setPhase({ stage: "uploading" });
    let draft: DraftCampaignWire;
    try {
      draft = entry.prepared
        ? await postDraftFromDataset(entry.dataset_name)
        : await postDraftFromOrigin(entry.origin_id);
    } catch (e) {
      setPhase({ stage: "idle" });
      pushError(e);
      return;
    }
    pushAi("Opened the origin — edit anything below, then Start.");
    setPhase({ stage: "ready", draft, resolution: null, raised: [], degradedCause: null });
  };

  // Only an unauthored draft re-runs the resolver: on an authored one it would re-propose the
  // L1 fields over the operator's edits.
  const reopenCheckin = async (campaignId: string) => {
    setMessages([]);
    setPhase({ stage: "uploading" });
    let res;
    try {
      res = await getCampaignCheckin(campaignId);
    } catch (e) {
      setPhase({ stage: "idle" });
      pushError(e);
      return;
    }
    const draft = res.draft;
    if (Object.keys(draft.origin_prompt_fields).length === 0) {
      pushAi("Reopened your check-in — picking up where the setup left off.");
      advance(draft);
      return;
    }
    pushAi("Reopened your check-in — finish the setup below, then Start.");
    setPhase({
      stage: "ready",
      draft,
      resolution: res.resolution,
      raised: res.raised,
      degradedCause: null,
    });
  };

  const submitContext = async () => {
    if (phase.stage !== "awaiting-context") return;
    const text = inputText.trim();
    if (!text) return;
    const draft = phase.draft;
    setInputText("");
    setMessages((m) => [...m, { id: uid(), kind: "user", text }]);
    let updated = draft;
    try {
      updated = await postEditDraftCampaign(draft.draft_id, { raw_task_description: text });
    } catch (e) {
      pushError(e);
      setPhase({ stage: "awaiting-context", draft });
      return;
    }
    await runCheckin(updated);
  };

  // THE seam for every ready-draft write: a mutator posting on its own escapes the await in
  // `startFromReady`, and the mint races the patch.
  const mutateDraft = async (
    send: () => Promise<DraftCampaignWire>,
  ): Promise<DraftCampaignWire | null> => {
    const write = send();
    pendingDraftWrites.current.add(write);
    setPendingPatches((n) => n + 1);
    try {
      const updated = await write;
      commitDraftUpdate(updated);
      return updated;
    } catch (e) {
      pushError(e);
      return null;
    } finally {
      pendingDraftWrites.current.delete(write);
      setPendingPatches((n) => n - 1);
    }
  };

  const applyPatch = (patch: DraftPatch) => {
    if (phase.stage !== "ready") return;
    void mutateDraft(() => postEditDraftCampaign(phase.draft.draft_id, patch));
  };

  // Never gates mint: the answers are already a runnable pool.
  const uploadCandidateLibrary = async (file: File) => {
    if (phase.stage !== "ready" || busy) return;
    const updated = await mutateDraft(() =>
      postUploadCandidateLibrary(phase.draft.draft_id, file),
    );
    if (updated) pushAi(`Candidate library attached — ${updated.candidate_library_size} targets.`);
  };

  const buildCandidateLibraryFromColumn = async (column: string) => {
    if (phase.stage !== "ready" || busy) return;
    const updated = await mutateDraft(() =>
      postBuildCandidateLibraryFromColumn(phase.draft.draft_id, column),
    );
    if (updated)
      pushAi(`Candidate library built from “${column}” — ${updated.candidate_library_size} targets.`);
  };

  const rerunCheckin = () => {
    if (phase.stage !== "ready" || busy) return;
    void runCheckin(phase.draft);
  };

  const startFromReady = async (limits: StartCheckinLimits) => {
    if (phase.stage !== "ready" || !phase.draft.readiness.complete) return;
    setMinting(true);
    try {
      // The starting tap can be the blur that sent a patch; a settled write enqueues nothing.
      while (pendingDraftWrites.current.size > 0) {
        await Promise.allSettled([...pendingDraftWrites.current]);
      }
      const r = await postStartCheckin(phase.draft.draft_id, limits);
      pushAi("Campaign started.");
      setPhase({ stage: "idle" });
      onMint({ campaignId: r.campaign_id, cycleId: r.cycle_id });
    } catch (e) {
      pushError(e);
    } finally {
      setMinting(false);
    }
  };

  const useExistingFromCollision = () => {
    if (phase.stage !== "collision") return;
    void draftFrom(phase.existingSlug, `Use existing dataset “${phase.existingSlug}”`);
  };

  const saveAsNew = () => {
    if (phase.stage !== "collision") return;
    void ingestAndResolve(phase.file, phase.suggestedSlug, phase.chipId);
  };

  // Data-safe: the server keeps the old data and every prior campaign under `{slug}-vN`.
  const replaceExisting = async () => {
    if (phase.stage !== "collision") return;
    const { file, existingSlug, chipId } = phase;
    setPhase({ stage: "uploading" });
    try {
      await postReplaceDataset(existingSlug);
    } catch (e) {
      setPhase({ stage: "idle" });
      pushError(e);
      return;
    }
    await ingestAndResolve(file, existingSlug, chipId);
  };

  const cancelCollision = () => setPhase({ stage: "idle" });

  // Idempotent per cycle: a live→stopped edge can be observed twice (a re-mount, a switch back).
  const pushRunSummary = (summary: RunSummary) =>
    setMessages((m) =>
      m.some((x) => x.kind === "run" && x.summary.cycleId === summary.cycleId)
        ? m
        : [...m, { id: uid(), kind: "run", summary }],
    );

  const reset = () => {
    setInputText("");
    setMessages([]);
    setPhase({ stage: "idle" });
  };

  return {
    messages,
    phase,
    inputText,
    setInputText,
    busy,
    saving: pendingPatches > 0,
    awaitingContext,
    onDatasetFile: (file) => void ingestAndResolve(file),
    pickDataset,
    openOrigin: (entry) => void openOrigin(entry),
    reopenCheckin: (campaignId) => void reopenCheckin(campaignId),
    submitContext: () => void submitContext(),
    applyPatch,
    uploadCandidateLibrary: (file) => void uploadCandidateLibrary(file),
    buildCandidateLibraryFromColumn: (column) => void buildCandidateLibraryFromColumn(column),
    rerunCheckin,
    startFromReady: (limits) => void startFromReady(limits),
    useExistingFromCollision,
    saveAsNew,
    replaceExisting: () => void replaceExisting(),
    cancelCollision,
    pushRunSummary,
    reset,
  };
}
