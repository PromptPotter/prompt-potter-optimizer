"use client";

import { useEffect, useId, useRef, useState } from "react";
import type { Budget, StartCheckinOptions } from "@/lib/api";
import { ELAPSED_TICK_MS } from "@/lib/cadence";
import { budgetAxis } from "@/lib/derivations";
import { useCollection } from "@/lib/hooks/useCollection";
import type { IngestFlow } from "@/lib/hooks/useIngestFlow";
import { useRegistry } from "@/lib/state/registry";
import type { BlockItem } from "@/lib/chat/thread";
import { useIngest } from "@/lib/ingest-flow";
import { Composer } from "@/components/chat/Composer";
import { useThreadFollow } from "@/components/chat/Thread";
import { Criterion } from "@/components/shell/scoring/Criterion";
import { FailureLine } from "@/components/ui";
import { dialsOf, dialsText } from "@/lib/scoring-mask";
import { ORIGIN_KEY } from "@/lib/origin-readiness";
import { DatasetPreview } from "./DatasetPreview";
import {
  SchemaFields,
  setIn,
  writeOnlyDefaults,
  type Commit,
  type FieldSchema,
} from "./SchemaFields";
import { ComposerTools } from "./ComposerTools";
import { SetupStep } from "./SetupStep";
import { PipelineSetupSection, pipelineLabel } from "./PipelineSetupSection";
import { OptimizerSetupSection } from "./OptimizerSetupSection";
import {
  HeadToHeadSetupSection,
  NO_HEAD_TO_HEAD,
  headToHeadLabel,
  startOptions,
  type HeadToHeadDraft,
} from "./HeadToHeadSetupSection";
import { PipelineDependencies } from "./PipelineDependencies";
import { OriginCheckinPanel } from "./OriginCheckinPanel";
import { DatasetPickList } from "./DatasetPickList";

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
  const counts =
    collection.kind === "ready"
      ? `${collection.origins.length} origins · ${collection.entries.length} datasets`
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
        {drafts.length > 0 ? `${drafts.length} open check-ins · ` : ""}
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

// Off a check-in it takes only a file, which opens a fresh one: a run's chat never answers a check-in.
export function IngestComposer() {
  const { flow, subject } = useIngest();
  const asking = subject !== null && flow.awaitingContext;
  return (
    <Composer
      value={asking ? flow.inputText : ""}
      onChange={flow.setInputText}
      onSubmit={flow.submitContext}
      canSend={asking}
      placeholder={asking ? "Describe the task…" : "Drop a dataset file…"}
      onFile={flow.attachFile}
      accept=".csv,.tsv,.tab,.txt,.json,.jsonl,.ndjson,.xlsx,.xlsm,.xls,.ods,text/csv,application/json"
      attachLabel="Attach a dataset file"
      attachDisabled={flow.busy}
      tools={<ComposerTools />}
    />
  );
}

function boundsLabel(launch: StartCheckinOptions, campaign: Budget): string {
  const usd = launch.budget?.usd;
  const spend = usd == null ? "account's cap" : `$${usd} cap`;
  const rounds = budgetAxis(campaign, "rounds");
  return `${spend} · ${rounds == null ? "no round cap" : `${rounds} round${rounds === 1 ? "" : "s"}`}`;
}

type Step = "data" | "scoring" | "pipeline" | "run";

// Which step answers a served gap; a field none answers opens nothing, and its hint still prints by Start.
const STEP_OF_GAP: Record<string, Step> = {
  [ORIGIN_KEY.columnQuery]: "data",
  [ORIGIN_KEY.columnGroundTruth]: "data",
  [ORIGIN_KEY.scoringMatcher]: "scoring",
  [ORIGIN_KEY.connector]: "pipeline",
  [ORIGIN_KEY.backendNodeConfig]: "pipeline",
};

function ReadyBlock({ flow }: { flow: IngestFlow }) {
  const blockersId = useId();
  // The schema's `writeOnly` subtree, already in `StartCheckinOptions` shape.
  const [launch, setLaunch] = useState<StartCheckinOptions>({});
  const [h2h, setH2h] = useState<HeadToHeadDraft>(NO_HEAD_TO_HEAD);
  const served = flow.phase.stage === "ready" ? flow.phase.draft : null;
  const [seededFor, setSeededFor] = useState<string | null>(null);
  if (served && served.draft_id !== seededFor) {
    setSeededFor(served.draft_id);
    setH2h(NO_HEAD_TO_HEAD);
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
  // `blocked` mirrors the server gate alone — never AND in `gaps.length`.
  const { complete: ready, gaps } = draft.readiness;
  const blocked = !ready;
  const gapped = new Set(gaps.map((g) => STEP_OF_GAP[g.field]));
  const columns =
    draft.column_query && draft.column_ground_truth
      ? `${draft.column_query} → ${draft.column_ground_truth}`
      : "columns not set";
  const steps = (draft.setup_schema as FieldSchema).properties ?? {};
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
        <OriginCheckinPanel
          draft={draft}
          lastResolution={resolution}
          raised={raised}
          onApply={flow.applyPatch}
        />
      ) : null}

      {/* The backbone leads, open: its knobs and prompts frame every step after it. */}
      <SetupStep title="Optimizer" answer={draft.optimization_overrides.optimizer} open>
        <OptimizerSetupSection draft={draft} onApply={flow.applyPatch} />
      </SetupStep>

      <SetupStep
        title="Data"
        answer={`${draft.slug} · ${draft.n_samples.toLocaleString()} rows · ${columns}`}
        open={gapped.has("data")}
      >
        {steps.data ? <SchemaFields schema={steps.data} values={draft} onCommit={commit} /> : null}
        <DatasetPreview draft={draft} />
      </SetupStep>

      <SetupStep title="Scoring" answer={draft.scoring_matcher} open={gapped.has("scoring")}>
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

      <SetupStep title="Pipeline" answer={pipelineLabel(draft)} open={gapped.has("pipeline")}>
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

      <SetupStep title="Run bounds" answer={boundsLabel(launch, draft.optimization_overrides.budget)} open={gapped.has("run")}>
        {steps.run ? (
          <SchemaFields schema={steps.run} values={{ ...draft, ...launch }} onCommit={commit} />
        ) : null}
      </SetupStep>

      <SetupStep title="Head-to-head" answer={headToHeadLabel(h2h)} open={false}>
        <HeadToHeadSetupSection value={h2h} onChange={setH2h} />
      </SetupStep>

      {/* A disabled button carries no tooltip on touch, so the reason must be text on the page. */}
      {blocked ? (
        <ul id={blockersId}>
          {gaps.length > 0 ? (
            gaps.map((g) => <li key={g.field}>{g.hint}</li>)
          ) : (
            <li>
              The setup isn’t ready to start yet — the server reported no specific field, so try
              re-running the check-in.
            </li>
          )}
        </ul>
      ) : null}

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
        {flow.busy ? "Starting…" : flow.saving ? "Saving…" : "Start campaign"}
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
