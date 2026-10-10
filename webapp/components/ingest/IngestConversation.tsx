"use client";

import { useEffect, useId, useState } from "react";
import type { DatasetIndexEntry, OriginEntry, StartCheckinOptions } from "@/lib/api";
import type { IngestFlow } from "@/lib/hooks/useIngestFlow";
import type { BlockItem } from "@/lib/chat/thread";
import { useIngest } from "@/lib/ingest-flow";
import { Composer } from "@/components/chat/Composer";
import { useThreadFollow } from "@/components/chat/Thread";
import { NumberField } from "@/components/ingest/NumberField";
import { SlugField } from "@/components/ingest/SlugField";
import { Criterion } from "@/components/shell/scoring/Criterion";
import { dialsOf, dialsText } from "@/lib/scoring-mask";
import { ColumnMappingPicker } from "./ColumnMappingPicker";
import { DatasetPreview } from "./DatasetPreview";
import { ComposerTools } from "./ComposerTools";
import { PipelineSetupSection } from "./PipelineSetupSection";
import { OptimizerSetupSection } from "./OptimizerSetupSection";
import { PipelineDependencies } from "./PipelineDependencies";
import { OriginCheckinPanel } from "./OriginCheckinPanel";
import { DatasetPickList } from "./DatasetPickList";

function CheckinLoadingWindow({ model }: { model: string }) {
  const [secs, setSecs] = useState(0);
  useEffect(() => {
    const id = setInterval(() => setSecs((s) => s + 1), 1000);
    return () => clearInterval(id);
  }, []);
  // The resolve is one blocking call, and a server-side repair retry can push it past a minute.
  const slow = secs >= 45;
  return (
    <p className="checkin-loading" role="status" aria-live="polite">
      <span className="checkin-loading-bot" aria-hidden="true">
        🤖
      </span>
      Check-in agent setting up · <span className="checkin-loading-model">{model}</span> · {secs}s
      {slow ? (
        <span className="checkin-loading-slow">
          {" "}— the model is slow right now; this can take a couple of minutes. I’ll
          flag it if the response comes back thin.
        </span>
      ) : null}
    </p>
  );
}

function EntryList({
  flow,
  origins,
  datasets,
  open,
}: {
  flow: IngestFlow;
  origins: OriginEntry[];
  datasets: DatasetIndexEntry[];
  // React writes `open` only when the PROP changes, so a poll tick leaves a hand-opened list alone.
  open: boolean;
}) {
  const follow = useThreadFollow();
  return (
    <details
      className="new-campaign-optional"
      open={open}
      onToggle={(e) => {
        if (e.currentTarget.open) follow.hold();
      }}
    >
      <summary>
        Start a campaign — {origins.length} origins · {datasets.length} datasets
      </summary>
      <DatasetPickList
        origins={origins}
        datasets={datasets}
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
        <p className="checkin-loading" role="status" aria-live="polite">
          Parsing your file…
        </p>
      ) : null}
      {phase.stage === "checkin" ? <CheckinLoadingWindow model={phase.model} /> : null}
      {phase.stage === "collision" ? (
        <CollisionCard flow={flow} existingSlug={phase.existingSlug} suggestedSlug={phase.suggestedSlug} />
      ) : null}
      {phase.stage === "ready" ? <ReadyBlock flow={flow} /> : null}

      {flow.awaitingContext ? (
        <div className="ingest-context-help" role="note">
          <p>
            Cover what each row means, what counts as a correct answer, and any
            rules or edge cases the model must respect — a few sentences is plenty.
          </p>
          {!flow.inputText.trim() ? (
            <p className="ingest-context-warning" role="alert">
              Context can’t be empty — the check-in needs it to set things up.
            </p>
          ) : null}
        </div>
      ) : null}
    </>
  );
}

export function useIngestItems(threadEmpty: boolean): { head: BlockItem[]; tail: BlockItem[] } {
  const { flow, collection } = useIngest();
  const head: BlockItem[] =
    flow.phase.stage === "idle" && collection.kind === "ready"
      ? [
          {
            id: "ingest-entry",
            kind: "block",
            node: (
              <EntryList
                flow={flow}
                origins={collection.origins}
                datasets={collection.entries}
                open={threadEmpty}
              />
            ),
          },
        ]
      : [];
  const tail: BlockItem[] = [
    { id: "ingest-setup", kind: "block", node: <SetupBlocks flow={flow} /> },
  ];
  return { head, tail };
}

export function IngestComposer() {
  const { flow } = useIngest();
  return (
    <Composer
      value={flow.inputText}
      onChange={flow.setInputText}
      onSubmit={flow.submitContext}
      canSend={flow.awaitingContext}
      placeholder={flow.awaitingContext ? "Describe the task…" : "Drop a dataset file…"}
      onFile={flow.onDatasetFile}
      accept=".csv,.tsv,.json,.jsonl,.ndjson,.xlsx,text/csv,application/json"
      attachLabel="Attach a dataset file"
      attachDisabled={flow.busy}
      tools={<ComposerTools />}
    />
  );
}

// Held as TEXT: a half-typed "0." is not a number. Blank means "no cap of mine".
interface CapDrafts {
  halt: string;
  usd: string;
  tokens: string;
}
const NO_CAPS: CapDrafts = { halt: "", usd: "", tokens: "" };

// Anything finite is SENT, out-of-range too: `StartCheckinPayload` owns the bounds and its 422 names the field.
function launchLimits(c: CapDrafts): StartCheckinOptions {
  const num = (s: string) => {
    const n = Number(s);
    return s.trim() !== "" && Number.isFinite(n) ? n : undefined;
  };
  const usd = num(c.usd) ?? null;
  const tokens = num(c.tokens) ?? null;
  return {
    halt_at_accuracy: num(c.halt),
    ceiling: usd === null && tokens === null ? undefined : { usd, tokens },
  };
}

function LaunchCaps({
  caps,
  onChange,
}: {
  caps: CapDrafts;
  onChange: (next: CapDrafts) => void;
}) {
  return (
    <>
      <label className="new-campaign-field">
        <span>Spend cap (USD)</span>
        <input
          type="number"
          min={0}
          step={0.5}
          inputMode="decimal"
          value={caps.usd}
          placeholder="account's own"
          aria-label="Spend cap in USD"
          onChange={(e) => onChange({ ...caps, usd: e.target.value })}
        />
      </label>
      <label className="new-campaign-field">
        <span>Token cap</span>
        <input
          type="number"
          min={0}
          step={1000}
          inputMode="numeric"
          value={caps.tokens}
          placeholder="account's own"
          aria-label="Token cap"
          onChange={(e) => onChange({ ...caps, tokens: e.target.value })}
        />
      </label>
      <label className="new-campaign-field">
        <span>Halt at accuracy</span>
        <input
          type="number"
          min={0}
          max={1}
          step={0.05}
          inputMode="decimal"
          value={caps.halt}
          placeholder="never halt on accuracy"
          aria-label="Halt at accuracy"
          onChange={(e) => onChange({ ...caps, halt: e.target.value })}
        />
      </label>
      <small>
        What THIS launch may spend — not saved with the setup, so a reopened check-in starts
        from blank. Whichever cap trips first stops the run; your account&apos;s own allowance
        still binds underneath.
      </small>
    </>
  );
}

function ReadyBlock({ flow }: { flow: IngestFlow }) {
  const blockersId = useId();
  const [caps, setCaps] = useState<CapDrafts>(NO_CAPS);
  if (flow.phase.stage !== "ready") return null;
  const { draft, resolution, raised, degradedCause } = flow.phase;
  // `blocked` mirrors the server gate alone — never AND in `gaps.length`.
  const { complete: ready, gaps } = draft.readiness;
  const blocked = !ready;

  return (
    <div className="chat-msg ai ingest-ready">
      {degradedCause ? (
        <div className="ingest-degraded" role="status">
          <p>
            The check-in came back degraded — {degradedCause}. Re-run it, or adjust the
            setup below by hand before starting.
          </p>
          <button
            type="button"
            className="chat-cta-btn secondary"
            disabled={flow.busy}
            onClick={flow.rerunCheckin}
          >
            {flow.busy ? "Re-running…" : "Re-run check-in"}
          </button>
        </div>
      ) : null}

      {!ready ? (
        <OriginCheckinPanel
          draft={draft}
          lastResolution={resolution}
          raised={raised}
          onApply={flow.applyPatch}
        />
      ) : null}

      <DatasetPreview draft={draft} />

      <ColumnMappingPicker draft={draft} onApply={flow.applyPatch} />

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

      <SlugField slug={draft.slug} onApply={(slug) => flow.applyPatch({ slug })} />

      <PipelineDependencies
        dependencies={draft.dependencies}
        librarySize={draft.candidate_library_size}
        headers={draft.headers}
        targetColumn={draft.column_ground_truth}
        onUpload={flow.uploadCandidateLibrary}
        onBuildFromColumn={flow.buildCandidateLibraryFromColumn}
        busy={flow.busy}
      />

      <PipelineSetupSection draft={draft} onApply={flow.applyPatch} />

      <OptimizerSetupSection draft={draft} onApply={flow.applyPatch} />

      <details className="new-campaign-optional ingest-advanced">
        {/* Max rounds patches the draft; the caps ride the Start press and are saved nowhere. */}
        <summary>Run bounds (optional)</summary>
        <div className="new-campaign-optional-body">
          <LaunchCaps caps={caps} onChange={setCaps} />
          <NumberField
            label="Max rounds"
            value={draft.optimization_overrides.max_rounds}
            min={0}
            max={100}
            onApply={(max_rounds) =>
              flow.applyPatch({ optimization_overrides: { max_rounds } })
            }
          />
        </div>
      </details>

      {/* A disabled button carries no tooltip on touch, so the reason must be text on the page. */}
      {blocked ? (
        <ul className="ingest-gap-list" id={blockersId}>
          {gaps.length > 0 ? (
            gaps.map((g) => (
              <li key={g.field} className="ingest-gap">
                {g.hint}
              </li>
            ))
          ) : (
            <li className="ingest-gap">
              The setup isn’t ready to start yet — the server reported no
              specific field, so try re-running the check-in.
            </li>
          )}
        </ul>
      ) : null}

      {/* `saving` never disables Start: that would eat the tap whose blur committed the field. */}
      <button
        type="button"
        className="chat-cta-btn"
        disabled={blocked || flow.busy}
        aria-describedby={blocked ? blockersId : undefined}
        onClick={() => flow.startFromReady(launchLimits(caps))}
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
    <div className="chat-msg ai chat-collision" role="group" aria-label="Dataset name already exists">
      <p>
        You already have a dataset named <strong>{existingSlug}</strong>. What would you like to do?
      </p>
      <div className="chat-collision-actions">
        <button type="button" className="chat-cta-btn" onClick={flow.useExistingFromCollision}>
          Use existing → new campaign
        </button>
        <button type="button" className="chat-cta-btn secondary" onClick={flow.saveAsNew}>
          Save as new ({suggestedSlug})
        </button>
        <button
          type="button"
          className="chat-cta-btn chat-collision-replace"
          title={`Archives the current ${existingSlug} (and its campaigns) as a version, then takes its place`}
          onClick={flow.replaceExisting}
        >
          Replace — keep old data as a version
        </button>
        <button type="button" className="chat-collision-cancel" onClick={flow.cancelCollision}>
          Cancel
        </button>
      </div>
    </div>
  );
}
