"use client";

import { useRef, useState } from "react";
import {
  ApiError,
  failureKind,
  operatorMessage,
  postDraftFromDataset,
  postDraftFromOrigin,
  getDraft,
  postCommand,
  postIngestDataset,
  postBuildCandidateLibraryFromColumn,
  postUploadCandidateLibrary,
  type DatasetIndexEntry,
  type DraftCampaignWire,
  type DraftPatch,
  type OriginEntry,
  type OriginLastResolution,
  type RaisedCommand,
  type StartCheckinOptions,
} from "@/lib/api";
import { useThread, type MessageTone } from "@/lib/chat/thread";
import { invalidateReads } from "@/lib/read-cache";
type OnMinted = (selection: { campaignId: string; cycleId: string }) => void;

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
      // A refused edit or Start, shown beside Start: the thread sits above the card, off a phone's screen.
      problem: string | null;
    };

export interface IngestFlow {
  phase: IngestPhase;
  inputText: string;
  setInputText: (v: string) => void;
  busy: boolean;
  // Never folded into `busy`: disabling Start eats the tap whose mousedown blurred the field.
  saving: boolean;
  awaitingContext: boolean;
  onDatasetFile: (file: File) => void;
  pickDataset: (entry: DatasetIndexEntry) => void;
  openOrigin: (entry: OriginEntry) => void;
  reopenDraft: (draftId: string) => void;
  discardDraft: (draftId: string) => void;
  submitContext: () => void;
  applyPatch: (patch: DraftPatch) => void;
  uploadCandidateLibrary: (file: File) => void;
  buildCandidateLibraryFromColumn: (column: string) => void;
  rerunCheckin: () => void;
  startFromReady: (limits: StartCheckinOptions) => void;
  useExistingFromCollision: () => void;
  saveAsNew: () => void;
  cancelCollision: () => void;
  reset: () => void;
}

// Instantiate ONCE, in `lib/ingest-flow.tsx`.
export function useIngestFlow({ onMint }: { onMint: OnMinted }): IngestFlow {
  const thread = useThread();
  const [phase, setPhase] = useState<IngestPhase>({ stage: "idle" });
  const [inputText, setInputText] = useState("");
  const [minting, setMinting] = useState(false);
  // A counter, not a boolean: the first overlapping patch to land must not clear a sibling's flag.
  const [pendingPatches, setPendingPatches] = useState(0);
  const pendingDraftWrites = useRef<Set<Promise<unknown>>>(new Set());

  const busy =
    phase.stage === "uploading" || phase.stage === "checkin" || minting;
  const awaitingContext = phase.stage === "awaiting-context";

  const say = (text: string, tone: MessageTone = "plain") =>
    thread.append({ kind: "message", role: "assistant", tone, text });
  const sayError = (e: unknown) => say(operatorMessage(e, failureKind(e)), "error");
  const echo = (text: string) =>
    thread.append({ kind: "message", role: "user", tone: "plain", text });

  // Functional updates, so overlapping patches cannot write a stale snapshot back.
  const commitDraftUpdate = (updated: DraftCampaignWire) =>
    setPhase((p) => (p.stage === "ready" ? { ...p, draft: updated, problem: null } : p));
  const refuse = (e: unknown) =>
    setPhase((p) =>
      p.stage === "ready" ? { ...p, problem: operatorMessage(e, failureKind(e)) } : p,
    );

  const runCheckin = async (draft: DraftCampaignWire) => {
    setPhase({ stage: "checkin", model: "the check-in model" });
    let resolved = draft;
    let resolution: OriginLastResolution | null = null;
    let raised: RaisedCommand[] = [];
    let degradedCause: string | null = null;
    let recap = "";
    try {
      const r = await postCommand("resolve-origin", { draft_id: draft.draft_id });
      resolved = r.draft;
      resolution = r.resolution.last_resolution;
      raised = r.resolution.raised;
      degradedCause = r.resolution.degraded_cause;
      recap = resolution?.recap || resolution?.assessment || resolved.recap;
    } catch (e) {
      recap = resolved.recap;
      sayError(e);
    }
    if (degradedCause) say(degradedCause, "warning");
    say(recap);
    // Even a complete draft lands in review: a launch spends money, so no path here mints.
    setPhase({ stage: "ready", draft: resolved, resolution, raised, degradedCause, problem: null });
  };

  const advance = (draft: DraftCampaignWire) => {
    invalidateReads("drafts");
    if (draft.raw_task_description.trim()) {
      setInputText("");
      say(`Parsed ${draft.n_samples} rows — task already on file. Checking the setup…`);
      void runCheckin(draft);
      return;
    }
    setInputText("");
    say(
      `Parsed ${draft.n_samples} rows. Describe the task — what should the model do with each row? The more you give me, the better I set up the prompt and pipeline. Send when ready.`,
    );
    setPhase({ stage: "awaiting-context", draft });
  };

  const ingestAndResolve = async (file: File, slug?: string, chipId?: string) => {
    // A refused drop must SAY so, or the previous campaign's draft reads as this file's.
    if (busy) {
      say(
        `“${file.name}” wasn’t picked up — the previous step was still loading. ` +
          `Nothing on screen is from this file; drop it again.`,
        "warning",
      );
      return;
    }
    const id = chipId ?? thread.append({ kind: "file", name: file.name, detail: null });
    setPhase({ stage: "uploading" });
    let draft: DraftCampaignWire;
    try {
      draft = await postIngestDataset(file, slug);
    } catch (e) {
      const taken = e instanceof ApiError && e.code === "slug_collision" ? e.details : null;
      if (typeof taken?.slug === "string" && typeof taken.suggested_slug === "string") {
        setPhase({
          stage: "collision",
          file,
          chipId: id,
          existingSlug: taken.slug,
          suggestedSlug: taken.suggested_slug,
        });
        return;
      }
      setPhase({ stage: "idle" });
      sayError(e);
      return;
    }
    thread.setFileDetail(id, `${draft.n_samples} rows`);
    advance(draft);
  };

  const draftFrom = async (name: string, label: string) => {
    if (busy) return;
    echo(label);
    setPhase({ stage: "uploading" });
    let draft: DraftCampaignWire;
    try {
      draft = await postDraftFromDataset(name);
    } catch (e) {
      setPhase({ stage: "idle" });
      sayError(e);
      return;
    }
    advance(draft);
  };

  const pickDataset = (entry: DatasetIndexEntry) =>
    void draftFrom(entry.name, `Use dataset “${entry.title || entry.name}”`);

  const openOrigin = async (entry: OriginEntry) => {
    if (busy) return;
    echo(`Reuse origin “${entry.label || entry.dataset_name}”`);
    setPhase({ stage: "uploading" });
    let draft: DraftCampaignWire;
    try {
      draft = await postDraftFromOrigin(entry.origin_id);
    } catch (e) {
      setPhase({ stage: "idle" });
      sayError(e);
      return;
    }
    say("Opened the origin — edit anything below, then Start.");
    setPhase({
      stage: "ready",
      draft,
      resolution: null,
      raised: [],
      degradedCause: null,
      problem: null,
    });
  };

  // Only an unauthored draft re-runs the resolver: it would re-propose over the operator's edits.
  const reopenDraft = async (draftId: string) => {
    thread.clear();
    setPhase({ stage: "uploading" });
    let res;
    try {
      res = await getDraft(draftId);
    } catch (e) {
      setPhase({ stage: "idle" });
      sayError(e);
      return;
    }
    const draft = res.draft;
    if (Object.keys(draft.origin_prompts).length === 0) {
      say("Reopened your check-in — picking up where the setup left off.");
      advance(draft);
      return;
    }
    say("Reopened your check-in — finish the setup below, then Start.");
    setPhase({
      stage: "ready",
      draft,
      resolution: res.resolution,
      raised: res.raised,
      degradedCause: null,
      problem: null,
    });
  };

  const submitContext = async () => {
    if (phase.stage !== "awaiting-context") return;
    const text = inputText.trim();
    if (!text) return;
    const draft = phase.draft;
    setInputText("");
    echo(text);
    let updated = draft;
    try {
      updated = await postCommand("edit-draft-campaign", {
        draft_id: draft.draft_id,
        patch: { raw_task_description: text },
      });
    } catch (e) {
      sayError(e);
      setPhase({ stage: "awaiting-context", draft });
      return;
    }
    await runCheckin(updated);
  };

  // Every ready-draft write rides this: one posting on its own escapes `startFromReady`'s await.
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
      refuse(e);
      return null;
    } finally {
      pendingDraftWrites.current.delete(write);
      setPendingPatches((n) => n - 1);
    }
  };

  const applyPatch = (patch: DraftPatch) => {
    if (phase.stage !== "ready") return;
    void mutateDraft(() =>
      postCommand("edit-draft-campaign", { draft_id: phase.draft.draft_id, patch }),
    );
  };

  const uploadCandidateLibrary = async (file: File) => {
    if (phase.stage !== "ready" || busy) return;
    const updated = await mutateDraft(() =>
      postUploadCandidateLibrary(phase.draft.draft_id, file),
    );
    if (updated) say(`Candidate library attached — ${updated.candidate_library_size} targets.`);
  };

  const buildCandidateLibraryFromColumn = async (column: string) => {
    if (phase.stage !== "ready" || busy) return;
    const updated = await mutateDraft(() =>
      postBuildCandidateLibraryFromColumn(phase.draft.draft_id, column),
    );
    if (updated)
      say(`Candidate library built from “${column}” — ${updated.candidate_library_size} targets.`);
  };

  const rerunCheckin = () => {
    if (phase.stage !== "ready" || busy) return;
    void runCheckin(phase.draft);
  };

  const startFromReady = async (limits: StartCheckinOptions) => {
    if (phase.stage !== "ready" || !phase.draft.readiness.complete) return;
    setMinting(true);
    try {
      // The starting tap can be the blur that sent a patch; a settled write enqueues nothing.
      while (pendingDraftWrites.current.size > 0) {
        await Promise.allSettled([...pendingDraftWrites.current]);
      }
      // Sparse, so the `CommandRecord` shows what the operator declared.
      const r = await postCommand("start-checkin", { draft_id: phase.draft.draft_id, ...limits });
      invalidateReads("drafts");
      setPhase({ stage: "idle" });
      // No hop while queued: the campaign is minted when a machine slot frees.
      if (r.hop === null) {
        say("Campaign queued — it is minted and starts when a machine slot frees.");
        return;
      }
      say("Campaign started.");
      onMint({ campaignId: r.hop.campaign_id, cycleId: r.hop.cycle_id });
    } catch (e) {
      refuse(e);
    } finally {
      setMinting(false);
    }
  };

  // Its spend stays banked to the account: a discard deletes the draft, never what it cost.
  const discardDraft = async (draftId: string) => {
    if (busy) return;
    try {
      await postCommand("discard-draft", { draft_id: draftId });
    } catch (e) {
      sayError(e);
      return;
    }
    invalidateReads("drafts");
    if ((phase.stage === "ready" || phase.stage === "awaiting-context") && phase.draft.draft_id === draftId)
      setPhase({ stage: "idle" });
  };

  const useExistingFromCollision = () => {
    if (phase.stage !== "collision") return;
    void draftFrom(phase.existingSlug, `Use existing dataset “${phase.existingSlug}”`);
  };

  const saveAsNew = () => {
    if (phase.stage !== "collision") return;
    void ingestAndResolve(phase.file, phase.suggestedSlug, phase.chipId);
  };

  const cancelCollision = () => setPhase({ stage: "idle" });

  const reset = () => {
    setInputText("");
    thread.clear();
    setPhase({ stage: "idle" });
  };

  return {
    phase,
    inputText,
    setInputText,
    busy,
    saving: pendingPatches > 0,
    awaitingContext,
    onDatasetFile: (file) => void ingestAndResolve(file),
    pickDataset,
    openOrigin: (entry) => void openOrigin(entry),
    reopenDraft: (draftId) => void reopenDraft(draftId),
    discardDraft: (draftId) => void discardDraft(draftId),
    submitContext: () => void submitContext(),
    applyPatch,
    uploadCandidateLibrary: (file) => void uploadCandidateLibrary(file),
    buildCandidateLibraryFromColumn: (column) => void buildCandidateLibraryFromColumn(column),
    rerunCheckin,
    startFromReady: (limits) => void startFromReady(limits),
    useExistingFromCollision,
    saveAsNew,
    cancelCollision,
    reset,
  };
}
