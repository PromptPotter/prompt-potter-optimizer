import { useEffect, useRef, useState } from "react";
import {
  commands,
  postDraftFromDataset,
  postDraftFromOrigin,
  postBuildAnswerTermsFromColumn,
  postUploadAnswerTerms,
  type StartCheckinOptions,
} from "@/lib/api";
import type {
  DatasetIndexEntry,
  DraftCampaignWire,
  DraftPatch,
  DraftReopenResponse,
  OriginEntry,
  OriginLastResolution,
  RaisedCommand,
  Tone,
} from "@/lib/api/types";
import { draftRead } from "@/lib/api/reads";
import { checkinThreadKey, useThreads } from "@/lib/state/chat-thread";
import { useCommand, type CommandFailure } from "@/lib/hooks/useCommand";
import { useQueuedStart } from "@/lib/hooks/useQueuedStart";
import { readyData, shownData, useRead } from "@/lib/hooks/useRead";
import { type ReadFailure, dropCachedRead, readResult } from "@/lib/read-cache";
type OnMinted = (selection: { campaignId: string; cycleId: string }) => void;

type IngestPhase =
  | { stage: "idle" }
  | { stage: "uploading" }
  // A draft waiting on its read's first answer: one this flow did not hold, or one it just opened.
  | { stage: "reopening" }
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
  // The served `DraftReopenResponse.start`: a Start holds the draft, queued on a slot or minting it.
  | { stage: NonNullable<DraftReopenResponse["start"]>["stage"] };

/** The check-in the workspace views: a draft id, `NEW_CHECKIN` before one is minted, null off one. */
export type CheckinSubject = string | null;

// A draft edit; one built from what the draft holds (a list toggled) is a function of the served
// draft, evaluated at its turn in the write queue, never a value fixed at the press.
export type DraftEdit = DraftPatch | ((served: DraftCampaignWire) => DraftPatch);
export const NEW_CHECKIN = "";

// The draft in review as its read serves it, beside the last resolver turn's proposals.
const review = (res: DraftReopenResponse): IngestPhase => ({
  stage: "ready",
  draft: res.draft,
  resolution: res.resolution.last_resolution,
  raised: res.resolution.raised,
  degradedCause: res.resolution.degraded_cause,
});

const QUEUED_LINE = "Campaign queued — it is minted and starts when a machine slot frees.";
const STARTING_LINE = "Campaign starting — it is being minted and opens in your list once it runs.";

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
  // The held draft's read, failed: a lost one retries on the reconnect beat, a refusal rests here.
  readFailure: ReadFailure | null;
  // A dataset file dropped anywhere: onto a fresh check-in, never into the viewed one's draft.
  attachFile: (file: File) => void;
  pickDataset: (entry: DatasetIndexEntry) => void;
  openOrigin: (entry: OriginEntry) => void;
  // A `discard-draft` its row sent landed: the flow lets go of that draft and its thread.
  discarded: (draftId: string) => void;
  submitContext: () => void;
  applyPatch: (edit: DraftEdit) => void;
  uploadAnswerTerms: (file: File) => void;
  buildAnswerTermsFromColumn: (column: string) => void;
  rerunCheckin: () => void;
  startFromReady: (limits: StartCheckinOptions) => void;
  useExistingFromCollision: () => void;
  saveAsNew: () => void;
  cancelCollision: () => void;
}

// Instantiate ONCE, in `lib/state/ingest-flow.tsx`. It holds the check-in of `subject`: moving to another
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
  // A queued Start's command, followed on the feed until a slot mints its campaign or it ends.
  const [following, setFollowing] = useState<string | null>(null);
  // How a queued Start ended unminted, beside Start as its own refusal would be.
  const [unminted, setUnminted] = useState<CommandFailure | null>(null);
  // Every write rides a slot; a draft's edits are a queue on one, so overlapping blurs land in order.
  const opening = useCommand<"upload" | "dataset" | "origin">("checkin-open", subject);
  const resolving = useCommand<"resolve">("checkin-resolve", draftId);
  const writing = useCommand<"edit" | "library">("checkin-edit", draftId);
  const starting = useCommand<"start">("checkin-start", draftId);
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
      setFollowing(null);
      setUnminted(null);
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
    phase.stage === "uploading" ||
    phase.stage === "reopening" ||
    phase.stage === "checkin" ||
    minting;
  const awaitingContext = phase.stage === "awaiting-context";

  const say = (text: string, tone: Tone = "quiet") =>
    threads.append(threadKey.current, { kind: "message", role: "assistant", tone, text });
  const echo = (text: string) =>
    threads.append(threadKey.current, { kind: "message", role: "user", tone: "quiet", text });

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

  // The served draft's one landing; functional, so a stage moved meanwhile keeps its own.
  const commitDraftUpdate = (updated: DraftCampaignWire) =>
    setPhase((p) =>
      p.stage === "ready" && p.draft.draft_id === updated.draft_id ? { ...p, draft: updated } : p,
    );

  // The held draft's one read (`draftRead`), held across stages, so no kept body re-lands.
  const draftNow = useRead(draftId !== null ? draftRead(draftId) : null);
  const served = readyData(draftNow);
  const [landedServed, setLandedServed] = useState(served);
  if (served !== landedServed) {
    setLandedServed(served);
    if (served) commitDraftUpdate(served.draft);
  }

  const queued = useQueuedStart(following);
  useEffect(() => {
    if (following === null || queued.stage === "waiting") return;
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setFollowing(null);
    if (queued.stage === "minted") {
      // The slot freed and minted it: the view moves onto the campaign, as an unqueued Start's does.
      setPhase({ stage: "idle" });
      setDraftId(null);
      onMint({ campaignId: queued.hop.campaign_id, cycleId: queued.hop.cycle_id });
      return;
    }
    // Refused when its slot freed, or withdrawn: the draft is back in review, the refusal beside Start.
    say(queued.said, "warn");
    setUnminted({ kind: "refused", message: queued.said, errorId: null, code: null, details: null });
    setPhase(served ? review(served) : { stage: "idle" });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [following, queued]);

  // The server decides whether a turn is bought (`resolver_not_owed`): only a re-run forces one.
  const runCheckin = async (draft: DraftCampaignWire, rerun = false) => {
    const alive = begin();
    setPhase({ stage: "checkin", model: "the check-in model" });
    const r = await resolving.run("resolve", () =>
      commands["resolve-origin"]({ draft_id: draft.draft_id, rerun }),
    );
    if (!alive()) return;
    // The ack answers once the draft's read stands on the turn, so the read is what lands; a
    // refused turn lands in review on the draft as served, its failure printed beside Start.
    const now = shownData(readResult(draftRead(draft.draft_id)));
    if (!now) return setPhase({ stage: "reopening" });
    const turn = r.ok ? now.resolution : null;
    if (turn?.degraded_cause) say(turn.degraded_cause, "warn");
    say(turn?.last_resolution?.recap || turn?.last_resolution?.assessment || now.draft.recap);
    // Even a complete draft lands in review: a launch spends money, so no path here mints.
    setPhase(review(now));
  };

  // A fresh draft lands as a reopened one does: its read's `resolution_pending` decides a turn.
  const advance = (draft: DraftCampaignWire) => {
    hold(draft.draft_id);
    setInputText("");
    say(
      draft.raw_task_description !== null
        ? `Parsed ${draft.n_samples} rows — task already on file.`
        : `Parsed ${draft.n_samples} rows. Describe the task — what should the model do with each row? The more you give me, the better I set up the prompt and pipeline. Or let me propose it from the rows.`,
    );
    setPhase({ stage: "reopening" });
  };

  const ingestAndResolve = async (file: File, slug?: string, chipId?: string) => {
    // A refused drop must SAY so, or the previous campaign's draft reads as this file's.
    if (busy) {
      say(
        `“${file.name}” wasn’t picked up — the previous step was still loading. ` +
          `Nothing on screen is from this file; drop it again.`,
        "warn",
      );
      return;
    }
    const alive = begin();
    const id =
      chipId ?? threads.append(threadKey.current, { kind: "file", name: file.name, detail: null });
    setPhase({ stage: "uploading" });
    const r = await opening.run("upload", () => commands["ingest-upload"](file, slug));
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
    // Landed as a fresh draft is, by its read.
    hold(r.value.draft_id);
    say("Opened the origin — edit anything below, then Start.");
    setPhase({ stage: "reopening" });
  };

  // A reload's thread, told from what the draft serves: each line is its state, never a memory.
  const retell = ({ draft, resolution }: DraftReopenResponse) => {
    // The task on file is the draft card's to show: the thread echoes only what the operator typed.
    say(
      `Reopened your check-in on “${draft.slug}” — ${draft.n_samples} rows parsed` +
        (draft.raw_task_description !== null ? ", task already on file." : "."),
    );
    if (resolution.degraded_cause) say(resolution.degraded_cause, "warn");
    const turn = resolution.last_resolution;
    const recap = turn?.recap || turn?.assessment;
    if (recap) say(recap);
    if (draft.answer_terms_size > 0)
      say(`Answer terms attached — ${draft.answer_terms_size} targets.`);
  };

  // Where the served draft stands, landed: `told` is false where the thread already says it.
  const land = (res: DraftReopenResponse, told: boolean) => {
    const draft = res.draft;
    if (res.start !== null) {
      if (told) say(res.start.stage === "queued" ? QUEUED_LINE : STARTING_LINE);
      setPhase({ stage: res.start.stage });
      // Followed as the tab that tapped Start follows it, until it mints or ends.
      setFollowing(res.start.command_id);
      return;
    }
    // No task on file: asked for first, as `resolution_pending` serves; an empty Send is the ask.
    if (draft.raw_task_description === null) {
      if (told)
        say("Describe the task — what should the model do with each row? Send when ready.");
      setPhase({ stage: "awaiting-context", draft });
      return;
    }
    // The one unasked turn: the served rule (`DraftFile.resolution_pending`), never re-bought.
    if (res.resolution_pending) {
      if (told) say("Picking up where the setup left off.");
      void runCheckin(draft);
      return;
    }
    if (told) say("Finish the setup below, then Start.");
    setPhase(review(res));
  };

  // A viewed draft this flow does not hold is read in: a deep link, a sidebar row, a reload.
  useEffect(() => {
    if (subject === null || subject === NEW_CHECKIN || subject === draftId) return;
    threadKey.current = checkinThreadKey(subject);
    // A reopen lands on a fresh answer, never a body kept from an earlier visit.
    dropCachedRead(draftRead(subject).id);
    // The address asks for a server read: the state it sets is that read's own start.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setDraftId(subject);
    setPhase({ stage: "reopening" });
  }, [subject, draftId]);

  // A revisit continues its kept thread; an empty one (a reload, a deep link) is retold.
  useEffect(() => {
    if (phase.stage !== "reopening") return;
    // A refusal rests on `readFailure`, a lost read retries; a gone draft leaves the check-in idle.
    if (draftNow.status === "failed" && draftNow.failure.kind === "gone") {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setPhase({ stage: "idle" });
      return;
    }
    if (draftNow.status !== "ready") return;
    const told = threads.of(threadKey.current).length === 0;
    if (told) retell(draftNow.data);
    land(draftNow.data, told);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [phase.stage, draftNow]);

  // Every draft write rides this queue: Start awaits its tail, so no edit escapes the launch. A
  // write's answer lands nothing: it answers once the draft's read stands on it (`commands.ts::acked`),
  // so *send* is built at its turn from the served draft, which then holds every earlier write.
  const mutateDraft = (
    verb: "edit" | "library",
    id: string,
    send: (served: DraftCampaignWire) => Promise<DraftCampaignWire>,
  ): Promise<DraftCampaignWire | null> => {
    const alive = begin();
    setPendingPatches((n) => n + 1);
    const landed = draftWrites.current.then(async () => {
      const served = shownData(readResult(draftRead(id)));
      const r = served && (await writing.run(verb, () => send(served.draft)));
      setPendingPatches((n) => n - 1);
      return r?.ok && alive() ? r.value : null;
    });
    draftWrites.current = landed;
    return landed;
  };

  const submitContext = async () => {
    if (phase.stage !== "awaiting-context") return;
    const text = inputText.trim();
    // Without one, the owed turn proposes the task from the rows: the one command `new <file>` sends.
    if (!text) return runCheckin(phase.draft);
    const id = phase.draft.draft_id;
    setInputText("");
    echo(text);
    const updated = await mutateDraft("edit", id, () =>
      commands["edit-draft-campaign"]({ draft_id: id, patch: { raw_task_description: text } }),
    );
    if (updated) await runCheckin(updated);
  };

  const applyPatch = (edit: DraftEdit) => {
    if (phase.stage !== "ready") return;
    const id = phase.draft.draft_id;
    void mutateDraft("edit", id, (served) =>
      commands["edit-draft-campaign"]({
        draft_id: id,
        patch: typeof edit === "function" ? edit(served) : edit,
      }),
    );
  };

  const uploadAnswerTerms = async (file: File) => {
    if (phase.stage !== "ready" || busy) return;
    const id = phase.draft.draft_id;
    const updated = await mutateDraft("library", id, () => postUploadAnswerTerms(id, file));
    if (updated) say(`Answer terms attached — ${updated.answer_terms_size} targets.`);
  };

  const buildAnswerTermsFromColumn = async (column: string) => {
    if (phase.stage !== "ready" || busy) return;
    const id = phase.draft.draft_id;
    const updated = await mutateDraft("library", id, () =>
      postBuildAnswerTermsFromColumn(id, column),
    );
    if (updated)
      say(`Answer terms built from “${column}” — ${updated.answer_terms_size} targets.`);
  };

  // Asked by the operator: the one turn bought whether or not the draft owes one.
  const rerunCheckin = () => {
    if (phase.stage !== "ready" || busy) return;
    void runCheckin(phase.draft, true);
  };

  const startFromReady = async (limits: StartCheckinOptions) => {
    if (phase.stage !== "ready" || !phase.draft.readiness.complete) return;
    const id = phase.draft.draft_id;
    const alive = begin();
    setUnminted(null);
    setMinting(true);
    // The starting tap can be the blur that queued an edit: the queue drains before Start sends.
    let tail: Promise<unknown>;
    do {
      tail = draftWrites.current;
      await tail;
    } while (tail !== draftWrites.current);
    // Sparse, so the filed `CommandRecord` shows what the operator declared.
    const r = await starting.run("start", () =>
      commands["start-checkin"]({ draft_id: id, ...limits }),
    );
    if (!r.ok || !alive()) {
      setMinting(false);
      return;
    }
    // One launch answer: a head-to-head's arms never queue, and the view opens on the first job.
    const [first] = r.value.launches;
    if (first === undefined) {
      setMinting(false);
      return;
    }
    const hop = first.hop;
    // No hop is the start's own answer that it queued: the draft stays the subject until a slot frees.
    if (hop === null) {
      say(QUEUED_LINE);
      setMinting(false);
      setPhase({ stage: "queued" });
      setFollowing(r.value.command_id);
      return;
    }
    setMinting(false);
    setPhase({ stage: "idle" });
    setDraftId(null);
    onMint({ campaignId: hop.campaign_id, cycleId: hop.cycle_id });
  };

  const discarded = (id: string) => {
    threads.clear(checkinThreadKey(id));
    if (draftId === id) {
      epoch.current += 1;
      setFollowing(null);
      setUnminted(null);
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
    failure:
      opening.failure ?? resolving.failure ?? writing.failure ?? starting.failure ?? unminted,
    readFailure: draftNow.status === "failed" ? draftNow.failure : null,
    attachFile,
    pickDataset,
    openOrigin: (entry) => void openOrigin(entry),
    discarded,
    submitContext: () => void submitContext(),
    applyPatch,
    uploadAnswerTerms: (file) => void uploadAnswerTerms(file),
    buildAnswerTermsFromColumn: (column) => void buildAnswerTermsFromColumn(column),
    rerunCheckin,
    startFromReady: (limits) => void startFromReady(limits),
    useExistingFromCollision,
    saveAsNew,
    cancelCollision,
  };
}
