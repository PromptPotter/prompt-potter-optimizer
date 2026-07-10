"use client";

import { useState } from "react";
import {
  IngestApiError,
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
  type OriginDegraded,
  type RaisedCommand,
} from "@/lib/api";
import { plainLanguageRecap } from "@/lib/origin-readiness";
import type { OnMinted } from "@/components/ingest/types";

// One durable chat message; the conversation renders from a list of these.
type ChatMsg =
  | { id: string; kind: "user-file"; name: string; rows: number | null }
  | { id: string; kind: "user"; text: string }
  | { id: string; kind: "ai"; text: string }
  // A non-fatal degradation notice (the check-in model hiccuped + retried). Loud
  // but not an error — the draft still resolved, just thinly.
  | { id: string; kind: "warning"; text: string }
  | { id: string; kind: "error"; text: string };

// Transient ingest-pipeline status, separate from the durable thread — it's
// replaced (not appended) as the pick/drop → context → check-in → ready
// sequence advances.
type IngestPhase =
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
  // A draft ready to commit. When `draft.readiness.complete` the view
  // shows "Start campaign"; otherwise it surfaces the remaining gaps inline
  // (check-in panel + column mapping) until the last one closes.
  | {
      stage: "ready";
      draft: DraftCampaignWire;
      resolution: OriginLastResolution | null;
      // Proposals the resolver left unclicked. The assistant offers them; only
      // the operator's click fires `edit-draft-campaign`.
      raised: RaisedCommand[];
      // Non-null when the resolver turn that produced this draft degraded — the
      // ready panel surfaces the warning + a "re-run check-in" affordance.
      degraded: OriginDegraded | null;
    };

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
  // Reuse an existing origin (from `GET /origins` — campaign-backed or prepared):
  // open it in the editable ready panel with NO check-in (the optimizer graph
  // enters at l1_generate, skipping checkin) — modify if wanted, then Start.
  openOrigin: (entry: OriginEntry) => void;
  // Re-open a durable `checkin`-lifecycle campaign from the sidebar: load its
  // draft + last resolver turn from disk straight into the editable ready panel.
  reopenCheckin: (campaignId: string) => void;
  // The operator's one-message task description (awaiting-context → check-in).
  submitContext: () => void;
  // Inline patch in the ready state (column mapping / question answers).
  applyPatch: (patch: DraftPatch) => void;
  // Drop the target list for an unfulfilled candidate_source dependency.
  uploadCandidateLibrary: (file: File) => void;
  // Build that library from one of the dataset's own columns instead of a file.
  buildCandidateLibraryFromColumn: (column: string) => void;
  // Re-run the check-in on the current ready draft (recovery after a degraded turn).
  rerunCheckin: () => void;
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
  const pushWarning = (text: string) =>
    setMessages((m) => [...m, { id: uid(), kind: "warning", text }]);

  // Commit a server draft into the LIVE ready phase. Functional update so the
  // rendered draft is always the latest server response (overlapping patches —
  // a pipeline-mode toggle + a library drop, the 2 s poll re-rendering between —
  // can't write a stale snapshot back). The whole ready panel, the dependency
  // card included, renders strictly from this draft, so it never lags the server:
  // switch to `llm_only` → server returns `dependencies: []` → the card drops.
  const commitDraftUpdate = (updated: DraftCampaignWire) =>
    setPhase((p) => (p.stage === "ready" ? { ...p, draft: updated } : p));
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
    let raised: RaisedCommand[] = [];
    let degraded: OriginDegraded | null = null;
    let recap = "";
    try {
      const r = await postResolveOrigin(draft.draft_id);
      resolved = r.draft;
      resolution = r.resolution.last_resolution ?? null;
      raised = r.resolution.raised ?? [];
      degraded = r.resolution.degraded ?? null;
      recap = resolution?.recap || resolution?.assessment || plainLanguageRecap(resolved);
    } catch (e) {
      recap = plainLanguageRecap(resolved);
      pushError(e);
    }
    // The resolver degraded (e.g. the model's first response was empty/truncated,
    // forcing a 2×-cost repair retry). Surface it loudly — the old behavior left
    // the operator watching a mute counter, then handed back a thin recap with no
    // sign anything had gone wrong.
    if (degraded) pushWarning(degraded.reasons.join(" · "));
    pushAi(recap);
    // Happy path: the check-in confirmed every gated field (columns + framing)
    // and asked nothing back — mint straight through, skipping the review
    // surface. The server gate stays authoritative (it also checks answer-space
    // and per-node model, which the client can't see from the wire), so a
    // rejected auto-mint falls through to the review panel below rather than
    // dead-ending on the error. A DEGRADED turn never auto-mints — a thin
    // resolution must land in review so the operator re-runs or fixes it by hand.
    if (
      !degraded &&
      resolution !== null &&
      resolution.next_action.questions.length === 0 &&
      resolved.readiness.complete
    ) {
      setMinting(true);
      try {
        const r = await postStartCheckin(resolved.draft_id);
        pushAi("Setup confirmed — campaign started.");
        setPhase({ stage: "idle" });
        onMint({ campaignId: r.campaign_id, cycleId: r.cycle_id });
        return;
      } catch (e) {
        pushError(e);
      } finally {
        setMinting(false);
      }
    }
    setPhase({ stage: "ready", draft: resolved, resolution, raised, degraded });
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

  // Reuse an origin: open it straight into the editable ready panel — NO
  // check-in LLM. A campaign-backed origin reproduces that origin's EXACT prompt
  // fields (and stamps `campaign_origin` lineage on mint) via `draft-from-origin`;
  // a prepared origin IS its dataset's current config, so the dataset-draft path
  // already reproduces it. The operator modifies if wanted and Starts, which mints
  // from the draft. Skips the checkin node; the graph enters at l1_generate.
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
    setPhase({ stage: "ready", draft, resolution: null, raised: [], degraded: null });
  };

  // Re-open a durable check-in: load its draft + last resolver turn from disk. The
  // campaign already exists (minted on the first ingest action) and survived
  // whatever happened since — a restart, a closed tab — which is the whole point of
  // making check-in durable. Finishing + Start flips it `checkin` → `active`.
  //
  // If the check-in agent never authored the origin prompt (minted on drop but
  // abandoned before the resolver ran — `origin_prompt_fields` still empty), RESUME
  // the authoring flow via `advance`: it runs the check-in so the agent proposes the
  // whole prompt + answer_format, exactly as a fresh drop would (no context yet →
  // ask for it first). Nothing is clobbered — the fields are blank. An already-
  // authored draft instead lands straight in the editable ready panel: re-running
  // the resolver would re-propose the L1 fields and overwrite the operator's edits.
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
    if (Object.keys(draft.origin_prompt_fields ?? {}).length === 0) {
      pushAi("Reopened your check-in — picking up where the setup left off.");
      advance(draft);
      return;
    }
    pushAi("Reopened your check-in — finish the setup below, then Start.");
    // Reopen is not a fresh resolve turn, so there's no live degradation to
    // surface — a re-run from the ready panel re-grades.
    setPhase({ stage: "ready", draft, resolution: res.resolution, raised: res.raised, degraded: null });
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
    try {
      const updated = await postEditDraftCampaign(phase.draft.draft_id, patch);
      commitDraftUpdate(updated);
    } catch (e) {
      pushError(e);
    }
  };

  // Drop a candidate library onto the ready draft → the unfulfilled
  // `candidate_source` dependency reads fulfilled. The library doesn't gate mint
  // (the answers are a runnable pool), so this never blocks Start — it sharpens
  // the candidate pool the backend ranks against.
  const uploadCandidateLibrary = async (file: File) => {
    if (phase.stage !== "ready" || busy) return;
    try {
      const updated = await postUploadCandidateLibrary(phase.draft.draft_id, file);
      pushAi(`Candidate library attached — ${updated.candidate_library_size} targets.`);
      commitDraftUpdate(updated);
    } catch (e) {
      pushError(e);
    }
  };

  // Build the candidate library from one of the dataset's own columns (no file).
  const buildCandidateLibraryFromColumn = async (column: string) => {
    if (phase.stage !== "ready" || busy) return;
    try {
      const updated = await postBuildCandidateLibraryFromColumn(phase.draft.draft_id, column);
      pushAi(`Candidate library built from “${column}” — ${updated.candidate_library_size} targets.`);
      commitDraftUpdate(updated);
    } catch (e) {
      pushError(e);
    }
  };

  // Recovery from a degraded turn: re-run the one check-in call on the current
  // draft. Reuses `runCheckin` (which re-enters the `checkin` phase, then back to
  // `ready` with the fresh resolution + degraded grade).
  const rerunCheckin = () => {
    if (phase.stage !== "ready" || busy) return;
    void runCheckin(phase.draft);
  };

  const startFromReady = async () => {
    if (phase.stage !== "ready" || !phase.draft.readiness.complete) return;
    setMinting(true);
    try {
      const r = await postStartCheckin(phase.draft.draft_id);
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
    reopenCheckin: (campaignId) => void reopenCheckin(campaignId),
    submitContext: () => void submitContext(),
    applyPatch: (patch) => void applyPatch(patch),
    uploadCandidateLibrary: (file) => void uploadCandidateLibrary(file),
    buildCandidateLibraryFromColumn: (column) => void buildCandidateLibraryFromColumn(column),
    rerunCheckin,
    startFromReady: () => void startFromReady(),
    useExistingFromCollision,
    saveAsNew,
    replaceExisting: () => void replaceExisting(),
    cancelCollision,
    reset,
  };
}
