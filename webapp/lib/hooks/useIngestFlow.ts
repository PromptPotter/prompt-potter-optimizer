"use client";

import { useEffect, useRef, useState } from "react";
import {
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
  type DraftReopenResponse,
  type OriginEntry,
  type OriginLastResolution,
  type RaisedCommand,
  type StartCheckinOptions,
} from "@/lib/api";
import { checkinThreadKey, useThreads, type MessageTone } from "@/lib/chat/thread";
import { useCommand, type CommandFailure } from "@/lib/hooks/useCommand";
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
    }
  // The served `DraftReopenResponse.queued`: a Start waits on a machine slot, minted once one frees.
  | { stage: "queued" };

/** The check-in the workspace views: a draft id, `NEW_CHECKIN` before one is minted, null off one. */
export type CheckinSubject = string | null;
export const NEW_CHECKIN = "";

const QUEUED_LINE = "Campaign queued — it is minted and starts when a machine slot frees.";

export interface IngestFlow {
  phase: IngestPhase;
  // The draft this flow holds; null before an upload or a pick mints one.
  draftId: string | null;
  inputText: string;
  setInputText: (v: string) => void;
  busy: boolean;
  // Never folded into `busy`: disabling Start eats the tap whose mousedown blurred the field.
  saving: boolean;
  awaitingContext: boolean;
  // The refused write, shown beside Start in review: the thread sits above the card, off a phone's screen.
  failure: CommandFailure | null;
  // A dataset file dropped anywhere: onto a fresh check-in, never into the viewed one's draft.
  attachFile: (file: File) => void;
  pickDataset: (entry: DatasetIndexEntry) => void;
  openOrigin: (entry: OriginEntry) => void;
  // A `discard-draft` its row sent landed: the flow lets go of that draft and its thread.
  discarded: (draftId: string) => void;
  submitContext: () => void;
  applyPatch: (patch: DraftPatch) => void;
  uploadCandidateLibrary: (file: File) => void;
  buildCandidateLibraryFromColumn: (column: string) => void;
  rerunCheckin: () => void;
  startFromReady: (limits: StartCheckinOptions) => void;
  useExistingFromCollision: () => void;
  saveAsNew: () => void;
  cancelCollision: () => void;
}

// Instantiate ONCE, in `lib/ingest-flow.tsx`. It holds the check-in of `subject`: moving to another
// check-in resets it, and every continuation of the one left behind is dropped (`alive`).
export function useIngestFlow({
  subject,
  onMint,
  onDraft,
  onDiscarded,
  onCompose,
}: {
  subject: CheckinSubject;
  onMint: OnMinted;
  // A fresh check-in minted its draft: the address moves onto it.
  onDraft: (draftId: string) => void;
  onDiscarded: (draftId: string) => void;
  // The address moves to a fresh check-in.
  onCompose: () => void;
}): IngestFlow {
  const threads = useThreads();
  const [phase, setPhase] = useState<IngestPhase>({ stage: "idle" });
  const [draftId, setDraftId] = useState<string | null>(null);
  const [inputText, setInputText] = useState("");
  const [minting, setMinting] = useState(false);
  // Every write rides a slot; a draft's edits are a queue on one, so overlapping blurs land in order.
  const opening = useCommand<"upload" | "dataset" | "origin">("checkin-open", { scope: subject });
  const resolving = useCommand<"resolve">("checkin-resolve", { scope: draftId, revalidate: false });
  const writing = useCommand<"edit" | "library">("checkin-edit", {
    scope: draftId,
    revalidate: false,
  });
  const starting = useCommand<"start">("checkin-start", { scope: draftId });
  // A counter, not a boolean: the first queued edit to land must not clear a sibling's flag.
  const [pendingPatches, setPendingPatches] = useState(0);
  const draftWrites = useRef<Promise<unknown>>(Promise.resolve());
  // The thread every message lands in: the held draft's, read at the call, never a stale closure's.
  const threadKey = useRef(checkinThreadKey(null));
  const epoch = useRef(0);
  const [resets, setResets] = useState(0);

  // Another check-in viewed: this one is let go (it stays durable on the server, reopened by id).
  const [prevSubject, setPrevSubject] = useState(subject);
  if (subject !== prevSubject) {
    setPrevSubject(subject);
    const held = draftId ?? NEW_CHECKIN;
    if (subject !== null && subject !== held) {
      setPhase({ stage: "idle" });
      setDraftId(null);
      setInputText("");
      setMinting(false);
      setResets((n) => n + 1);
    }
  }
  useEffect(() => {
    epoch.current += 1;
    threadKey.current = checkinThreadKey(null);
    // A fresh check-in starts empty, so an abandoned one's lines never greet the next.
    if (resets > 0) threads.clear(checkinThreadKey(null));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [resets]);
  const begin = () => {
    const at = epoch.current;
    return () => epoch.current === at;
  };

  const busy =
    phase.stage === "uploading" || phase.stage === "checkin" || minting;
  const awaitingContext = phase.stage === "awaiting-context";

  const say = (text: string, tone: MessageTone = "plain") =>
    threads.append(threadKey.current, { kind: "message", role: "assistant", tone, text });
  const echo = (text: string) =>
    threads.append(threadKey.current, { kind: "message", role: "user", tone: "plain", text });

  // The flow now holds *id*: a fresh check-in's thread moves under it, and the address follows.
  const hold = (id: string) => {
    const key = checkinThreadKey(id);
    if (threadKey.current !== key) {
      threads.move(threadKey.current, key);
      threadKey.current = key;
    }
    setDraftId(id);
    onDraft(id);
  };

  // Functional updates, so overlapping patches cannot write a stale snapshot back.
  const commitDraftUpdate = (updated: DraftCampaignWire) =>
    setPhase((p) =>
      p.stage === "ready" && p.draft.draft_id === updated.draft_id ? { ...p, draft: updated } : p,
    );

  const runCheckin = async (draft: DraftCampaignWire) => {
    const alive = begin();
    setPhase({ stage: "checkin", model: "the check-in model" });
    const r = await resolving.run("resolve", () =>
      postCommand("resolve-origin", { draft_id: draft.draft_id }),
    );
    if (!alive()) return;
    // A refused turn lands in review on the draft as it stood; its failure prints beside Start.
    const resolved = r.ok ? r.value.draft : draft;
    const resolution = r.ok ? r.value.resolution.last_resolution : null;
    const degradedCause = r.ok ? r.value.resolution.degraded_cause : null;
    if (degradedCause) say(degradedCause, "warning");
    say(resolution?.recap || resolution?.assessment || resolved.recap);
    // Even a complete draft lands in review: a launch spends money, so no path here mints.
    setPhase({
      stage: "ready",
      draft: resolved,
      resolution,
      raised: r.ok ? r.value.resolution.raised : [],
      degradedCause,
    });
  };

  const advance = (draft: DraftCampaignWire) => {
    hold(draft.draft_id);
    setInputText("");
    if (draft.raw_task_description.trim()) {
      say(`Parsed ${draft.n_samples} rows — task already on file. Checking the setup…`);
      void runCheckin(draft);
      return;
    }
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
    const alive = begin();
    const id =
      chipId ?? threads.append(threadKey.current, { kind: "file", name: file.name, detail: null });
    setPhase({ stage: "uploading" });
    const r = await opening.run("upload", () => postIngestDataset(file, slug));
    if (!alive()) return;
    if (!r.ok) {
      // The one refusal whose next step is a choice: it opens the collision card, not a failure line.
      const taken = r.failure?.code === "slug_collision" ? r.failure.details : null;
      if (typeof taken?.slug === "string" && typeof taken.suggested_slug === "string") {
        opening.clear();
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
      return;
    }
    threads.setFileDetail(threadKey.current, id, `${r.value.n_samples} rows`);
    advance(r.value);
  };

  const draftFrom = async (name: string, label: string) => {
    if (busy) return;
    const alive = begin();
    echo(label);
    setPhase({ stage: "uploading" });
    const r = await opening.run("dataset", () => postDraftFromDataset(name));
    if (!alive()) return;
    if (!r.ok) {
      setPhase({ stage: "idle" });
      return;
    }
    advance(r.value);
  };

  const pickDataset = (entry: DatasetIndexEntry) =>
    void draftFrom(entry.name, `Use dataset “${entry.title || entry.name}”`);

  const openOrigin = async (entry: OriginEntry) => {
    if (busy) return;
    const alive = begin();
    echo(`Reuse origin “${entry.label || entry.dataset_name}”`);
    setPhase({ stage: "uploading" });
    const r = await opening.run("origin", () => postDraftFromOrigin(entry.origin_id));
    if (!alive()) return;
    if (!r.ok) {
      setPhase({ stage: "idle" });
      return;
    }
    hold(r.value.draft_id);
    say("Opened the origin — edit anything below, then Start.");
    setPhase({ stage: "ready", draft: r.value, resolution: null, raised: [], degradedCause: null });
  };

  // A reload's thread, told from what the draft serves: each line is its state, never a memory.
  const retell = ({ draft, resolution }: DraftReopenResponse) => {
    say(`Reopened your check-in on “${draft.slug}” — ${draft.n_samples} rows parsed.`);
    if (draft.raw_task_description.trim()) echo(draft.raw_task_description);
    const recap = resolution?.recap || resolution?.assessment;
    if (recap) say(recap);
    if (draft.candidate_library_size > 0)
      say(`Candidate library attached — ${draft.candidate_library_size} targets.`);
  };

  // Where the served draft stands, landed: `told` is false where the thread already says it.
  const land = (res: DraftReopenResponse, told: boolean) => {
    const draft = res.draft;
    if (res.queued) {
      if (told) say(QUEUED_LINE);
      setPhase({ stage: "queued" });
      return;
    }
    if (!draft.raw_task_description.trim()) {
      if (told)
        say("Describe the task — what should the model do with each row? Send when ready.");
      setPhase({ stage: "awaiting-context", draft });
      return;
    }
    // The resolver runs only where no turn ever set it up: never re-bought, never over edits.
    if (res.resolution_pending) {
      if (told) say("Picking up where the setup left off.");
      void runCheckin(draft);
      return;
    }
    if (told) say("Finish the setup below, then Start.");
    setPhase({
      stage: "ready",
      draft,
      resolution: res.resolution,
      raised: res.raised,
      degradedCause: null,
    });
  };

  const readDraft = async (id: string, alive: () => boolean) => {
    try {
      return await getDraft(id);
    } catch (e) {
      if (alive())
        say(operatorMessage(e, failureKind(e)), "error");
      return null;
    }
  };

  // A revisit continues its kept thread; an empty one (a reload, a deep link) is retold.
  const reopenDraft = async (id: string) => {
    const alive = begin();
    threadKey.current = checkinThreadKey(id);
    const told = threads.of(threadKey.current).length === 0;
    setDraftId(id);
    setPhase({ stage: "uploading" });
    const res = await readDraft(id, alive);
    if (!alive()) return;
    if (res === null) {
      setPhase({ stage: "idle" });
      return;
    }
    if (told) retell(res);
    land(res, told);
  };

  // A viewed draft this flow does not hold is read in: a deep link, a sidebar row, a reload.
  useEffect(() => {
    if (subject === null || subject === NEW_CHECKIN || subject === draftId) return;
    // A server read the address asks for: the state it sets is that read's own landing.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void reopenDraft(subject);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [subject, draftId]);

  // Every draft write rides this queue: Start awaits its tail, so no edit escapes the launch.
  const mutateDraft = (
    verb: "edit" | "library",
    send: () => Promise<DraftCampaignWire>,
  ): Promise<DraftCampaignWire | null> => {
    const alive = begin();
    setPendingPatches((n) => n + 1);
    const landed = draftWrites.current.then(async () => {
      const r = await writing.run(verb, send, commitDraftUpdate);
      setPendingPatches((n) => n - 1);
      return r.ok && alive() ? r.value : null;
    });
    draftWrites.current = landed;
    return landed;
  };

  const submitContext = async () => {
    if (phase.stage !== "awaiting-context") return;
    const text = inputText.trim();
    if (!text) return;
    const id = phase.draft.draft_id;
    setInputText("");
    echo(text);
    const updated = await mutateDraft("edit", () =>
      postCommand("edit-draft-campaign", { draft_id: id, patch: { raw_task_description: text } }),
    );
    if (updated) await runCheckin(updated);
  };

  const applyPatch = (patch: DraftPatch) => {
    if (phase.stage !== "ready") return;
    const id = phase.draft.draft_id;
    void mutateDraft("edit", () => postCommand("edit-draft-campaign", { draft_id: id, patch }));
  };

  const uploadCandidateLibrary = async (file: File) => {
    if (phase.stage !== "ready" || busy) return;
    const id = phase.draft.draft_id;
    const updated = await mutateDraft("library", () => postUploadCandidateLibrary(id, file));
    if (updated) say(`Candidate library attached — ${updated.candidate_library_size} targets.`);
  };

  const buildCandidateLibraryFromColumn = async (column: string) => {
    if (phase.stage !== "ready" || busy) return;
    const id = phase.draft.draft_id;
    const updated = await mutateDraft("library", () =>
      postBuildCandidateLibraryFromColumn(id, column),
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
    const id = phase.draft.draft_id;
    const alive = begin();
    setMinting(true);
    // The starting tap can be the blur that queued an edit: the queue drains before Start sends.
    let tail: Promise<unknown>;
    do {
      tail = draftWrites.current;
      await tail;
    } while (tail !== draftWrites.current);
    // Sparse, so the `CommandRecord` shows what the operator declared.
    const r = await starting.run("start", () =>
      postCommand("start-checkin", { draft_id: id, ...limits }),
    );
    if (!r.ok || !alive()) {
      setMinting(false);
      return;
    }
    // No hop while queued: the draft stays the subject, landed on what its reopen serves.
    if (r.value.hop === null) {
      say(QUEUED_LINE);
      const res = await readDraft(id, alive);
      setMinting(false);
      if (res !== null && alive()) land(res, false);
      return;
    }
    setMinting(false);
    setPhase({ stage: "idle" });
    setDraftId(null);
    onMint({ campaignId: r.value.hop.campaign_id, cycleId: r.value.hop.cycle_id });
  };

  const discarded = (id: string) => {
    threads.clear(checkinThreadKey(id));
    if (draftId === id) {
      epoch.current += 1;
      setPhase({ stage: "idle" });
      setDraftId(null);
      threadKey.current = checkinThreadKey(null);
    }
    onDiscarded(id);
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

  // Handed over once the fresh check-in is the subject, so the upload lands in its thread.
  const [pendingFile, setPendingFile] = useState<File | null>(null);
  const attachFile = (file: File) => {
    if (subject === NEW_CHECKIN) {
      void ingestAndResolve(file);
      return;
    }
    setPendingFile(file);
    onCompose();
  };
  useEffect(() => {
    if (pendingFile === null || subject !== NEW_CHECKIN) return;
    // The upload the address move was waiting on: the state it sets is that upload's own landing.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void ingestAndResolve(pendingFile);
    setPendingFile(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pendingFile, subject]);

  return {
    phase,
    draftId,
    inputText,
    setInputText,
    busy,
    saving: pendingPatches > 0,
    awaitingContext,
    failure: opening.failure ?? resolving.failure ?? writing.failure ?? starting.failure,
    attachFile,
    pickDataset,
    openOrigin: (entry) => void openOrigin(entry),
    discarded,
    submitContext: () => void submitContext(),
    applyPatch,
    uploadCandidateLibrary: (file) => void uploadCandidateLibrary(file),
    buildCandidateLibraryFromColumn: (column) => void buildCandidateLibraryFromColumn(column),
    rerunCheckin,
    startFromReady: (limits) => void startFromReady(limits),
    useExistingFromCollision,
    saveAsNew,
    cancelCollision,
  };
}
