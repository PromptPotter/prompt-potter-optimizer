"use client";

import { useEffect, useId, useRef, useState } from "react";
import type { StartCheckinOptions } from "@/lib/api";
import type { SetupStep as Step } from "@/lib/api/types.generated";
import { backendHealthRead } from "@/lib/api/reads";
import { CONNECTOR_HEALTH_POLL_MS, ELAPSED_TICK_MS } from "@/lib/cadence";
import { BACKEND_REACHABILITY_INFO, COUPON_STATE_INFO } from "@/lib/api/types.generated";
import { stepAnswer, type FieldSchema } from "@/lib/derivations";
import { useCollection } from "@/lib/hooks/useCollection";
import { useMachineStatus } from "@/lib/hooks/useMachineStatus";
import { readyData, useRead } from "@/lib/hooks/useRead";
import type { IngestFlow } from "@/lib/hooks/useIngestFlow";
import { useRegistry } from "@/lib/state/registry";
import type { BlockItem } from "@/lib/chat/thread";
import { useIngest } from "@/lib/ingest-flow";
import { useWorkspace } from "@/lib/workspace";
import { familiesNamed } from "@/components/shell/families";
import { Composer } from "@/components/chat/Composer";
import { useThreadFollow } from "@/components/chat/Thread";
import { Criterion } from "@/components/shell/scoring/Criterion";
import { FailureLine, Loading, Said } from "@/components/ui";
import { dialsOf, dialsText } from "@/lib/scoring-mask";
import { DatasetPreview } from "./DatasetPreview";
import { SchemaFields, setIn, writeOnlyDefaults, type Commit } from "./SchemaFields";
import { ComposerTools } from "./ComposerTools";
import { SetupStep } from "./SetupStep";
import { PipelineSetupSection, pipelineLabel } from "./PipelineSetupSection";
import { OptimizerSetupSection } from "./OptimizerSetupSection";
import { PipelineDependencies } from "./PipelineDependencies";
import { OriginCheckinPanel } from "./OriginCheckinPanel";
import { DatasetPickList } from "./DatasetPickList";
import { HeadToHeadSection, startOptions, type HeadToHeadDraft } from "./HeadToHeadArms";

function CheckinLoadingWindow({ model }: { model: string }) {
  const [secs, setSecs] = useState(0);
  useEffect(() => {
    const id = setInterval(() => setSecs((s) => s + 1), ELAPSED_TICK_MS);
    return () => clearInterval(id);
  }, []);
  return (
    <p role="status" aria-live="polite">
      Check-in agent setting up · {model} · {secs}s
      {/* The resolve is one blocking call, and a server-side repair retry can push it past a minute. */}
      {secs >= 45
        ? " — the model is slow right now; this can take a couple of minutes. I’ll flag it if the response comes back thin."
        : null}
    </p>
  );
}

function EntryList({
  flow,
  onReopenDraft,
  open,
}: {
  flow: IngestFlow;
  onReopenDraft: (draftId: string) => void;
  // React writes `open` only when the PROP changes, so a poll tick leaves a hand-opened list alone.
  open: boolean;
}) {
  const follow = useThreadFollow();
  const drafts = useRegistry().unstarted;
  // Read only while the list is on screen: a run viewed never waits on, or polls, either.
  const collection = useCollection();
  if (collection.kind === "needsAuth") return null;
  const count = (n: number, noun: string) => `${n} ${noun}${n === 1 ? "" : "s"}`;
  const counts =
    collection.kind === "ready"
      ? `${count(collection.origins.length, "origin")} · ${count(collection.entries.length, "dataset")}`
      : "origins and datasets";
  return (
    <details
      open={open}
      onToggle={(e) => {
        if (e.currentTarget.open) follow.hold();
      }}
    >
      <summary>
        Start a campaign —{" "}
        {drafts.length > 0 ? `${count(drafts.length, "open check-in")} · ` : ""}
        {counts}
      </summary>
      <DatasetPickList
        drafts={drafts}
        collection={collection}
        onReopenDraft={onReopenDraft}
        onDiscarded={flow.discarded}
        onOpenOrigin={flow.openOrigin}
        onPick={flow.pickDataset}
        busy={flow.busy}
      />
    </details>
  );
}

function SetupBlocks({ flow }: { flow: IngestFlow }) {
  const { phase } = flow;
  return (
    <>
      {phase.stage === "uploading" ? (
        <p role="status" aria-live="polite">
          Parsing your file…
        </p>
      ) : null}
      {phase.stage === "reopening" ? <Loading>Reading your check-in…</Loading> : null}
      {phase.stage === "checkin" ? <CheckinLoadingWindow model={phase.model} /> : null}
      {phase.stage === "collision" ? (
        <CollisionCard flow={flow} existingSlug={phase.existingSlug} suggestedSlug={phase.suggestedSlug} />
      ) : null}
      {/* Review prints its refusal beside Start; every other stage prints it here. */}
      {phase.stage === "ready" ? <ReadyBlock flow={flow} /> : <FailureLine failure={flow.failure} />}

      {flow.awaitingContext ? (
        <div role="note">
          <p>
            Cover what each row means, what counts as a correct answer, and any rules or edge
            cases the model must respect — a few sentences is plenty.
          </p>
          {!flow.inputText.trim() ? (
            <FailureLine
              failure={{ message: "Context can’t be empty — the check-in needs it to set things up." }}
            />
          ) : null}
        </div>
      ) : null}
    </>
  );
}

// The check-in's own blocks; the pane mounts them only while a check-in is the subject.
export function useIngestItems(threadEmpty: boolean): { head: BlockItem[]; tail: BlockItem[] } {
  const { flow, openDraft } = useIngest();
  const head: BlockItem[] =
    flow.phase.stage === "idle"
      ? [
          {
            id: "ingest-entry",
            kind: "block",
            node: <EntryList flow={flow} onReopenDraft={openDraft} open={threadEmpty} />,
          },
        ]
      : [];
  const tail: BlockItem[] = [
    { id: "ingest-setup", kind: "block", node: <SetupBlocks flow={flow} /> },
  ];
  return { head, tail };
}

// Off a check-in a file opens a fresh one, and text is command words: the view labels it names open.
export function IngestComposer() {
  const { flow, subject } = useIngest();
  const { open } = useWorkspace();
  const [words, setWords] = useState("");
  const onRun = subject === null;
  const asking = !onRun && flow.awaitingContext;
  const say = () => {
    const named = familiesNamed(words);
    if (named === null) return;
    for (const family of named) open(family);
    setWords("");
  };
  return (
    <Composer
      value={onRun ? words : asking ? flow.inputText : ""}
      onChange={onRun ? setWords : flow.setInputText}
      onSubmit={onRun ? say : flow.submitContext}
      canSend={onRun || asking}
      placeholder={onRun ? "Name a view, or drop a file…" : asking ? "Describe the task…" : "Drop a dataset file…"}
      onFile={flow.attachFile}
      accept=".csv,.tsv,.tab,.txt,.json,.jsonl,.ndjson,.xlsx,.xlsm,.xls,.ods,text/csv,application/json"
      attachLabel="Attach a dataset file"
      attachDisabled={flow.busy}
      tools={<ComposerTools />}
    />
  );
}

function ReadyBlock({ flow }: { flow: IngestFlow }) {
  const blockersId = useId();
  const notice = readyData(useMachineStatus())?.notice ?? null;
  // The schema's `writeOnly` subtree, already in `StartCheckinOptions` shape.
  const [launch, setLaunch] = useState<StartCheckinOptions>({});
  const served = flow.phase.stage === "ready" ? flow.phase.draft : null;
  // The probe the run control reads, on the connector this draft would launch.
  const health = readyData(
    useRead(served ? backendHealthRead(served.connector) : null, {
      auth: true,
      intervalMs: CONNECTOR_HEALTH_POLL_MS,
    }),
  );
  const [h2h, setH2h] = useState<HeadToHeadDraft>({ id: "", arms: [] });
  const [seededFor, setSeededFor] = useState<string | null>(null);
  if (served && served.draft_id !== seededFor) {
    setSeededFor(served.draft_id);
    setH2h({ id: `${served.slug}-h2h`, arms: [] });
    // Per step: a step's key is no patch path, so its fields commit from their own root.
    const stepSchemas = Object.values((served.setup_schema as FieldSchema).properties ?? {});
    setLaunch(
      Object.assign({}, ...stepSchemas.map((s) => writeOnlyDefaults(s))) as StartCheckinOptions,
    );
  }
  // Start sits right below the refusal, so scrolling to it shows both.
  const startRef = useRef<HTMLButtonElement>(null);
  const failure = flow.failure;
  useEffect(() => {
    if (failure) startRef.current?.scrollIntoView({ block: "nearest" });
  }, [failure]);
  if (flow.phase.stage !== "ready") return null;
  const { draft, resolution, raised, degradedCause } = flow.phase;
  // `blocked` mirrors the two served gates alone: the origin's and the allowance's at the declared budget.
  const { complete: ready, gaps } = draft.readiness;
  const refusal = draft.launch_refusal;
  const backend = health === null || health.status === "live" ? null : BACKEND_REACHABILITY_INFO[health.status];
  const blocked = !ready || refusal !== null;
  const gapped = new Set<Step | null>(gaps.map((g) => g.step));
  const steps = (draft.setup_schema as FieldSchema).properties ?? {};
  const runValues = { ...draft, ...launch };
  const commit: Commit = (path, value, writeOnly) => {
    if (writeOnly) setLaunch((l) => setIn(l, path, value));
    else flow.applyPatch(setIn({}, path, value));
  };

  return (
    <div className="chat-msg ai ingest-ready">
      {degradedCause ? (
        <p role="status">
          The check-in came back degraded — {degradedCause}. Re-run it, or adjust the setup
          below by hand before starting.{" "}
          <button type="button" disabled={flow.busy} onClick={flow.rerunCheckin}>
            {flow.busy ? "Re-running…" : "Re-run check-in"}
          </button>
        </p>
      ) : null}

      {!ready ? (
        <OriginCheckinPanel lastResolution={resolution} raised={raised} onApply={flow.applyPatch} />
      ) : null}

      {/* The backbone leads: its knobs and prompts frame every step after it. */}
      <SetupStep step="optimizer" answer={draft.optimization_overrides.optimizer} gapped={gapped}>
        <OptimizerSetupSection draft={draft} onApply={flow.applyPatch} />
      </SetupStep>

      <SetupStep
        step="data"
        answer={steps.data ? stepAnswer(steps.data, draft) : null}
        gapped={gapped}
      >
        {steps.data ? <SchemaFields schema={steps.data} values={draft} onCommit={commit} /> : null}
        <DatasetPreview draft={draft} />
      </SetupStep>

      <SetupStep step="scoring" answer={draft.scoring_matcher} gapped={gapped}>
        <Criterion
          startRung={1}
          dialsOnly
          mask={{ kind: "dials", weights: dialsOf(draft.scoring_dials) }}
          onMask={(mask) => {
            if (mask.kind === "dials") flow.applyPatch({ scoring_dials: dialsText(mask.weights) });
          }}
          matcher={{
            value: draft.scoring_matcher,
            options: draft.scoring_matchers,
            onPick: (scoring_matcher) => flow.applyPatch({ scoring_matcher }),
          }}
          note="What this campaign optimizes for. Accuracy always counts; pull a dial up to also reward a faster, shorter or cheaper prompt. Each anchor is measured on the origin and locked there."
        />
      </SetupStep>

      <SetupStep step="pipeline" answer={pipelineLabel(draft)} gapped={gapped}>
        <PipelineSetupSection draft={draft} onApply={flow.applyPatch}>
          <PipelineDependencies
            dependencies={draft.dependencies}
            librarySize={draft.candidate_library_size}
            headers={draft.headers}
            targetColumn={draft.column_ground_truth}
            onUpload={flow.uploadCandidateLibrary}
            onBuildFromColumn={flow.buildCandidateLibraryFromColumn}
            busy={flow.busy}
          />
        </PipelineSetupSection>
      </SetupStep>

      <SetupStep
        step="head_to_head"
        answer={h2h.arms.length === 0 ? "one campaign" : h2h.arms.map((a) => a.key).join(" vs ")}
        gapped={gapped}
      >
        <HeadToHeadSection
          value={h2h}
          onChange={setH2h}
          base={draft.optimization_overrides.optimizer}
          setup={draft.optimizer_setup}
        />
      </SetupStep>

      <SetupStep step="run" answer={steps.run ? stepAnswer(steps.run, runValues) : null} gapped={gapped}>
        {steps.run ? <SchemaFields schema={steps.run} values={runValues} onCommit={commit} /> : null}
      </SetupStep>

      {/* A disabled button carries no tooltip on touch, so the reason must be text on the page. */}
      {blocked ? (
        <ul id={blockersId}>
          {gaps.map((g) => (
            <li key={g.field}>{g.hint}</li>
          ))}
          {refusal ? (
            <li>
              <Said
                tone={COUPON_STATE_INFO[refusal.state].tone}
                label={COUPON_STATE_INFO[refusal.state].label}
                detail={refusal.reason}
              />
            </li>
          ) : null}
        </ul>
      ) : null}

      {notice ? <Said tone={notice.tone} label={notice.label} detail={notice.detail} /> : null}
      {backend ? <Said tone={backend.tone} label={backend.label} /> : null}

      <FailureLine failure={failure} />

      {/* `saving` never disables Start: that would eat the tap whose blur committed the field. */}
      <button
        ref={startRef}
        type="button"
        className="chat-cta-btn"
        disabled={blocked || flow.busy}
        aria-describedby={blocked ? blockersId : undefined}
        onClick={() => flow.startFromReady(startOptions(launch, h2h))}
      >
        {flow.busy
          ? "Starting…"
          : flow.saving
            ? "Saving…"
            : h2h.arms.length > 0
              ? `Start ${h2h.arms.length} arms`
              : "Start campaign"}
      </button>
    </div>
  );
}

function CollisionCard({
  flow,
  existingSlug,
  suggestedSlug,
}: {
  flow: IngestFlow;
  existingSlug: string;
  suggestedSlug: string;
}) {
  return (
    <div className="chat-msg ai" role="group" aria-label="Dataset name already exists">
      <p>
        You already have a dataset named <strong>{existingSlug}</strong>. What would you like to do?
      </p>
      <p>
        <button type="button" onClick={flow.useExistingFromCollision}>
          Use existing → new campaign
        </button>{" "}
        <button type="button" onClick={flow.saveAsNew}>
          Save as new ({suggestedSlug})
        </button>{" "}
        <button type="button" onClick={flow.cancelCollision}>
          Cancel
        </button>
      </p>
    </div>
  );
}
