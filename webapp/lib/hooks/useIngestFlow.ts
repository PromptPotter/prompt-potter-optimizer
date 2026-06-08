"use client";

import { useState } from "react";
import {
  IngestApiError,
  postDraftFromDataset,
  postEditDraftCampaign,
  postIngestDataset,
  postMintCampaignFromDraft,
  postReplaceDataset,
  postResolveOrigin,
  type DatasetIndexEntry,
  type DraftCampaignWire,
  type DraftPatch,
  type OriginLastResolution,
} from "@/lib/api";
import { originReadiness, plainLanguageRecap } from "@/lib/origin-readiness";
import type { OnMinted } from "@/components/ingest/types";

// One durable chat message; the conversation renders from a list of these.
export type ChatMsg =
  | { id: string; kind: "user-file"; name: string; rows: number | null }
  | { id: string; kind: "user"; text: string }
  | { id: string; kind: "ai"; text: string }
  | { id: string; kind: "error"; text: string };

// Transient ingest-pipeline status, separate from the durable thread — it's
// replaced (not appended) as the pick/drop → context → check-in → ready
// sequence advances.
export type IngestPhase =
  | { stage: "idle" }
  | { stage: "uploading" }
  // Parsed/picked. The chat always surfaces the context box here — prefilled
  // with whatever task the dataset already carries — so the operator confirms
  // or refines it before the one check-in call. Context is required: the origin
  // check-in needs it to configure anything, so we never burn the call empty.
  | { stage: "awaiting-context"; draft: DraftCampaignWire }
  | { stage: "checkin"; model: string }
  // A dropped file's name matches a dataset already in the collection. The chat
  // offers the safe choices (use existing / save as new / replace) rather than
  // a dead-end 409.
  | {
      stage: "collision";
      file: File;
      chipId: string;
      existingSlug: string;
      suggestedSlug: string;
    }
  // A draft ready to commit. When `originReadiness(draft).complete` the view
  // shows "Start campaign"; otherwise it surfaces the remaining gaps inline
  // (check-in panel + column mapping) until the last one closes.
  | { stage: "ready"; draft: DraftCampaignWire; resolution: OriginLastResolution | null };

export interface IngestFlow {
  messages: ChatMsg[];
  phase: IngestPhase;
  inputText: string;
  setInputText: (v: string) => void;
  busy: boolean;
  awaitingContext: boolean;
  // Drop or attach a tabular file → upload → readiness branch.
  onDatasetFile: (file: File) => void;
  // Make a NEW origin for a dataset via the check-in LLM (for when the operator
  // has no origin in mind yet) → editable ready panel → Start.
  pickDataset: (entry: DatasetIndexEntry) => void;
  // Reuse a dataset's committed origin: open it in the editable ready panel with
  // NO check-in (the optimizer graph enters at l1_generate, skipping checkin) —
  // modify if wanted, then Start.
  openOrigin: (entry: DatasetIndexEntry) => void;
  // The operator's one-message task description (awaiting-context → check-in).
  submitContext: () => void;
  // Inline patch in the ready state (column mapping / question answers).
  applyPatch: (patch: DraftPatch) => void;
  // Commit the ready draft + spawn the runner.
  startFromReady: () => void;
  // Collision choices.
  useExistingFromCollision: () => void;
  saveAsNew: () => void;
  replaceExisting: () => void;
  cancelCollision: () => void;
  // Clear the conversation back to idle (the modal calls this on open).
  reset: () => void;
}

const uid = () => crypto.randomUUID();

// The single dataset → origin → campaign state machine, shared by the "New
// campaign" modal and the dashboard chat tab. One path: pick a dataset OR drop
// a file → ask for context only if it's missing → ONE check-in call → Start.
// `onMint` fires with the new (campaign, cycle) once the runner is spawned; the
// caller decides the side effects (select the cycle, close the modal).
export function useIngestFlow({ onMint }: { onMint: OnMinted }): IngestFlow {
  const [messages, setMessages] = useState<ChatMsg[]>([]);
  const [phase, setPhase] = useState<IngestPhase>({ stage: "idle" });
  const [inputText, setInputText] = useState("");
  const [minting, setMinting] = useState(false);

  const busy =
    phase.stage === "uploading" || phase.stage === "checkin" || minting;
  const awaitingContext = phase.stage === "awaiting-context";

  const pushAi = (text: string) =>
    setMessages((m) => [...m, { id: uid(), kind: "ai", text }]);
  const pushError = (e: unknown) =>
    setMessages((m) => [
      ...m,
      { id: uid(), kind: "error", text: IngestApiError.toOperatorMessage(e) },
    ]);

  // The origin check-in — the single LLM call that configures the draft from the
  // operator's context. The check-in failing is non-fatal: the draft still
  // exists, so we land in `ready` and the view surfaces the gaps.
  const runCheckin = async (draft: DraftCampaignWire) => {
    setPhase({ stage: "checkin", model: "the check-in model" });
    let resolved = draft;
    let resolution: OriginLastResolution | null = null;
    let recap = "";
    try {
      const r = await postResolveOrigin(draft.draft_id);
      resolved = r.draft;
      resolution = r.resolution.last_resolution ?? null;
      recap = resolution?.recap || resolution?.assessment || plainLanguageRecap(resolved);
    } catch (e) {
      recap = plainLanguageRecap(resolved);
      pushError(e);
    }
    pushAi(recap);
    setPhase({ stage: "ready", draft: resolved, resolution });
  };

  // After any draft-producing action (drop / pick). If the dataset already
  // carries its task — a registered dataset, or a dropped file whose dataloader
  // the backend recognized — there's nothing to ask: run the one check-in
  // straight through to the Start button. Only a context-less drop stops to ask,
  // prefilling the box and waiting for the operator's one message.
  const advance = (draft: DraftCampaignWire) => {
    if ((draft.raw_task_description ?? "").trim()) {
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

  // Upload a tabular file → readiness branch. A 409 name collision is NOT an
  // error — it routes to the choice card. `slug` re-runs ingest under a chosen
  // name ("save as new"); `chipId` reuses the rendered file chip.
  const ingestAndResolve = async (file: File, slug?: string, chipId?: string) => {
    if (busy) return;
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

  // Open a registered dataset as a draft → same context ask as a drop, prefilled
  // with the dataset's known task description.
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

  // Reuse a dataset's committed origin: open the draft (which carries the
  // committed origin fields + pipeline config) straight into the editable ready
  // panel — NO check-in LLM. The operator modifies if wanted and Starts, which
  // mints from the draft. Skips the checkin node; the graph enters at l1_generate.
  const openOrigin = async (entry: DatasetIndexEntry) => {
    if (busy) return;
    setMessages((m) => [
      ...m,
      { id: uid(), kind: "user", text: `Reuse origin “${entry.title || entry.name}”` },
    ]);
    setPhase({ stage: "uploading" });
    let draft: DraftCampaignWire;
    try {
      draft = await postDraftFromDataset(entry.name);
    } catch (e) {
      setPhase({ stage: "idle" });
      pushError(e);
      return;
    }
    pushAi("Opened the committed origin — edit anything below, then Start.");
    setPhase({ stage: "ready", draft, resolution: null });
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

  const applyPatch = async (patch: DraftPatch) => {
    if (phase.stage !== "ready") return;
    const { draft, resolution } = phase;
    try {
      const updated = await postEditDraftCampaign(draft.draft_id, patch);
      setPhase({ stage: "ready", draft: updated, resolution });
    } catch (e) {
      pushError(e);
    }
  };

  const startFromReady = async () => {
    if (phase.stage !== "ready" || !originReadiness(phase.draft).complete) return;
    setMinting(true);
    try {
      const r = await postMintCampaignFromDraft(phase.draft.draft_id);
      pushAi("Campaign started.");
      setPhase({ stage: "idle" });
      onMint({ campaignId: r.campaign_id, cycleId: r.cycle_id });
    } catch (e) {
      pushError(e);
    } finally {
      setMinting(false);
    }
  };

  // Collision: start a new campaign on the dataset already in the collection —
  // routes through the same readiness branch as any other dataset pick.
  const useExistingFromCollision = () => {
    if (phase.stage !== "collision") return;
    void draftFrom(phase.existingSlug, `Use existing dataset “${phase.existingSlug}”`);
  };

  // Collision: save the dropped file under the suggested free name.
  const saveAsNew = () => {
    if (phase.stage !== "collision") return;
    void ingestAndResolve(phase.file, phase.suggestedSlug, phase.chipId);
  };

  // Collision: version-and-repoint the existing dataset so its name frees, then
  // re-ingest the dropped file under it. Data-safe — old data + every prior
  // campaign's results are preserved under `{slug}-vN`.
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
    awaitingContext,
    onDatasetFile: (file) => void ingestAndResolve(file),
    pickDataset,
    openOrigin: (entry) => void openOrigin(entry),
    submitContext: () => void submitContext(),
    applyPatch: (patch) => void applyPatch(patch),
    startFromReady: () => void startFromReady(),
    useExistingFromCollision,
    saveAsNew,
    replaceExisting: () => void replaceExisting(),
    cancelCollision,
    reset,
  };
}
