"use client";

import { useEffect, useRef, useState, type ReactNode } from "react";
import type { DatasetIndexEntry, OriginEntry } from "@/lib/api";
import type { IngestFlow } from "@/lib/hooks/useIngestFlow";
import { cx } from "@/lib/cx";
import { NumberField } from "@/components/forms/NumberField";
import { SlugField } from "@/components/forms/SlugField";
import { MechanismsPanel } from "@/components/dashboard/control/MechanismsPanel";
import { ColumnMappingPicker } from "./ColumnMappingPicker";
import { PipelineSetupSection } from "./PipelineSetupSection";
import { PipelineDependencies } from "./PipelineDependencies";
import { OriginCheckinPanel } from "./OriginCheckinPanel";
import { DatasetPickList } from "./DatasetPickList";

// The dropped-file bubble in the chat thread — "filename.csv · N rows". Reuses
// the existing `.chat-msg.user-file` / `.file-chip` styles (app/styles/domains/
// chat.css). `rows` is null until the upload resolves (n_samples comes back from
// `postIngestDataset`), so the row count appears once the file is parsed.
function ChatFileChip({ name, rows }: { name: string; rows: number | null }) {
  return (
    <div className="chat-msg user user-file">
      <div className="file-chip">
        <svg
          width="18"
          height="18"
          viewBox="0 0 18 18"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.5"
          strokeLinecap="round"
          strokeLinejoin="round"
          aria-hidden="true"
        >
          <path d="M14.5 7.5 8 14a3.5 3.5 0 0 1-4.95-4.95L9.5 2.6a2.4 2.4 0 0 1 3.4 3.4L6.4 12.5a1.3 1.3 0 0 1-1.83-1.83L11 4.2" />
        </svg>
        <span className="name">{name}</span>
        {rows != null && <span className="meta">· {rows} rows</span>}
      </div>
    </div>
  );
}

// Inline "check-in agent working" line — 🤖 + the model + a seconds counter.
// Shown while the real resolve runs and during the demo's simulation.
function CheckinLoadingWindow({ model }: { model: string }) {
  const [secs, setSecs] = useState(0);
  useEffect(() => {
    const id = setInterval(() => setSecs((s) => s + 1), 1000);
    return () => clearInterval(id);
  }, []);
  // The resolve is a single blocking call; a slow provider — or a 2×-cost repair
  // retry firing under the hood — can run a minute-plus. Past a threshold, set
  // expectations so the counter isn't a mute "is it stuck?": reassure the wait is
  // normal and that a degraded turn gets reported, never silently swallowed.
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

// The single ingest conversation, driven by `useIngestFlow`. Rendered by BOTH
// the "New campaign" modal (`variant="modal"`, with the dataset entry list) and
// the dashboard chat tab (`variant="inline"`, with the first-run illustration).
// One thread: pick/drop → ask context only if missing → one check-in → Start →
// then the live cycle's curated activity + decisions (`liveSegment`).
export function IngestConversation({
  flow,
  origins,
  datasets,
  variant,
  liveSegment,
}: {
  flow: IngestFlow;
  // Only the modal supplies the entry lists: existing origins to reuse +
  // datasets to make a new origin from.
  origins?: OriginEntry[];
  datasets?: DatasetIndexEntry[];
  variant: "modal" | "inline";
  // The live tail (curated activity feed + decision buttons) appended into the
  // thread once a cycle is bound. Present only on the inline chat tab. When set
  // and showing content it also collapses the welcome illustration — the thread
  // must never render the placeholder over live activity.
  liveSegment?: ReactNode;
}) {
  const { phase, messages } = flow;
  const [dragging, setDragging] = useState(false);
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  const showEntryList =
    variant === "modal" && phase.stage === "idle" && datasets !== undefined;
  const showIllustration =
    variant === "inline" &&
    messages.length === 0 &&
    phase.stage === "idle" &&
    !liveSegment;

  return (
    <div className={cx("ingest-conversation", `ingest-conversation--${variant}`)}>
      <div className="chat-messages" aria-live="polite">
        {showIllustration ? <FirstRunIllustration /> : null}
        {showEntryList ? (
          <DatasetPickList
            origins={origins ?? []}
            datasets={datasets!}
            onOpenOrigin={flow.openOrigin}
            onPick={flow.pickDataset}
            busy={flow.busy}
          />
        ) : null}

        {messages.map((msg) =>
          msg.kind === "user-file" ? (
            <ChatFileChip key={msg.id} name={msg.name} rows={msg.rows} />
          ) : msg.kind === "user" ? (
            <div key={msg.id} className="chat-msg user">
              {msg.text}
            </div>
          ) : msg.kind === "ai" ? (
            <div key={msg.id} className="chat-msg ai">
              {msg.text}
            </div>
          ) : msg.kind === "warning" ? (
            <div key={msg.id} className="chat-msg ai chat-msg-warn" role="status">
              {msg.text}
            </div>
          ) : (
            <div key={msg.id} className="chat-msg ai chat-msg-error" role="alert">
              {msg.text}
            </div>
          ),
        )}

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

        {/* The live cycle's curated activity + inline decisions — the same
            ordered thread, continued. */}
        {liveSegment}
      </div>

      <div
        className={cx("chat-input-row", dragging && "is-dragover")}
        onDragOver={(e) => {
          e.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragging(false);
          const f = e.dataTransfer.files[0];
          if (f) flow.onDatasetFile(f);
        }}
      >
        <input
          ref={fileInputRef}
          type="file"
          hidden
          accept=".csv,.tsv,.json,.jsonl,.ndjson,.xlsx,text/csv,application/json"
          onChange={(e) => {
            const f = e.target.files?.[0];
            if (f) flow.onDatasetFile(f);
            e.target.value = "";
          }}
        />
        <button
          className="chat-attach"
          type="button"
          title="Attach a dataset file"
          aria-label="Attach a dataset file"
          disabled={flow.busy}
          onClick={() => fileInputRef.current?.click()}
        >
          <svg width="18" height="18" viewBox="0 0 18 18" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
            <path d="M14.5 7.5 8 14a3.5 3.5 0 0 1-4.95-4.95L9.5 2.6a2.4 2.4 0 0 1 3.4 3.4L6.4 12.5a1.3 1.3 0 0 1-1.83-1.83L11 4.2" />
          </svg>
        </button>
        <textarea
          className="chat-input"
          placeholder={
            flow.awaitingContext
              ? "Describe the task in one message…"
              : "Drop a CSV, TSV, JSON or Excel file…"
          }
          rows={1}
          value={flow.inputText}
          onChange={(e) => flow.setInputText(e.target.value)}
          onKeyDown={(e) => {
            if (flow.awaitingContext && e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              flow.submitContext();
            }
          }}
          disabled={!flow.awaitingContext}
          aria-label="Chat input"
        />
        <button
          className="chat-send"
          type="button"
          disabled={!flow.awaitingContext || !flow.inputText.trim()}
          onClick={() => flow.submitContext()}
        >
          Send
        </button>
      </div>
    </div>
  );
}

// The ready state: confirm the origin before Start. Any remaining gaps surface
// inline (check-in assessment + questions, column mapping). The starting prompt
// and pipeline are shown expanded + prefilled for confirmation — they're the
// origin the operator is about to evolve. Only the meta-optimizer knobs (which
// model drives the search, run bounds) collapse into an optional expander.
function ReadyBlock({ flow }: { flow: IngestFlow }) {
  if (flow.phase.stage !== "ready") return null;
  const { draft, resolution, raised, degraded } = flow.phase;
  const { complete: ready, gaps } = draft.readiness;

  return (
    <div className="chat-msg ai ingest-ready">
      {degraded ? (
        <div className="ingest-degraded" role="status">
          <p>
            The check-in came back degraded — {degraded.reasons.join(" · ")}. Re-run
            it, or adjust the setup below by hand before starting.
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
        <div className="ingest-gaps">
          <OriginCheckinPanel
            draft={draft}
            lastResolution={resolution}
            raised={raised}
            onApply={flow.applyPatch}
          />
          {/* The server gate's open fields, each with its operator-facing hint.
              The resolver panel above only covers gaps it raised a question for
              (answer_format / answer_space it can leave silently empty), so this
              list is the honest "what still blocks Start" — resolved by editing
              the prompt / mapping below until the gate clears. */}
          {gaps.length > 0 ? (
            <ul className="ingest-gap-list">
              {gaps.map((g) => (
                <li key={g.field} className="ingest-gap">
                  {g.hint}
                </li>
              ))}
            </ul>
          ) : null}
        </div>
      ) : null}

      <ColumnMappingPicker draft={draft} onApply={flow.applyPatch} />

      {/* The active pipeline's required inputs beyond (pipeline + dataset +
          origin) — e.g. a candidate_source node's target library — surfaced so
          the operator drops the missing one in place. Soft: doesn't gate Start. */}
      <PipelineDependencies
        dependencies={draft.dependencies}
        librarySize={draft.candidate_library_size}
        headers={draft.headers}
        targetColumn={draft.column_ground_truth}
        onUpload={flow.uploadCandidateLibrary}
        onBuildFromColumn={flow.buildCandidateLibraryFromColumn}
        busy={flow.busy}
      />

      {/* Per node: the optimizer search-space controls (lock/allow + origin
          value) and, for the LLM node, the starting prompt — all inside that
          node's surface (config → prompt → output), editable — the one
          NodeSurface every node-detail surface renders. */}
      <PipelineSetupSection draft={draft} onApply={flow.applyPatch} />

      <details className="new-campaign-optional ingest-advanced">
        <summary>Optimizer (optional)</summary>
        <div className="new-campaign-optional-body">
          <NumberField
            label="Max rounds"
            value={draft.optimization_overrides.max_rounds}
            min={1}
            max={100}
            onApply={(max_rounds) =>
              flow.applyPatch({ optimization_overrides: { max_rounds } })
            }
          />
          <SlugField slug={draft.slug} onApply={(slug) => flow.applyPatch({ slug })} />
          {/* Pluggable orchestration mechanisms — sorting/selection + early-abort
              toggles, the same surface the dashboard renders read-only. Editable
              here at authoring time; each flip patches the draft's campaign.json. */}
          <MechanismsPanel
            mechanisms={draft.optimization_overrides.mechanisms}
            onChange={(mechanisms) =>
              flow.applyPatch({ optimization_overrides: { mechanisms } })
            }
          />
        </div>
      </details>

      <button
        type="button"
        className="chat-cta-btn"
        disabled={!ready || flow.busy}
        onClick={flow.startFromReady}
      >
        {flow.busy ? "Starting…" : "Start campaign"}
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

// First-run illustration — shown on the dashboard chat tab until the operator
// drops a file, so a newcomer sees what the surface is for. Replaced by the live
// ingest thread on first drop.
function FirstRunIllustration() {
  return (
    <>
      <div className="chat-msg user">My pipeline above is stuck at 73%. Can&apos;t push past that.</div>
      <div className="chat-msg ai">Share the eval set you&apos;re scoring against?</div>
      <ChatFileChip name="email-tagging.csv" rows={15} />
      <div className="chat-msg ai">Got your pipeline + the project. Flip on Auto-tune (BETA) — I&apos;ll find a better prompt for it. Want me to turn it on?</div>
      <div className="chat-msg ai">Which parameter do you want to explore?</div>
      <div className="chat-msg ai">
        Few things to tune this right:<br />
        • <strong>Which evaluators matter most?</strong> Easy picks: speed (time per query), # of websites checked, accuracy, cost per query — pick whatever you care about.<br />
        • <strong>Preferred LLM</strong>, or should I pick one?<br />
        • <strong>Pipeline type</strong> — LLM-driven (a model decides each step) or deterministic (fixed rules, no AI in the loop)?<br />
        • <strong>Time ceiling</strong> — any hard cap on how long one query is allowed to take?
      </div>
    </>
  );
}
