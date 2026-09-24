"use client";

import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import type { DatasetIndexEntry, OriginEntry, StartCheckinLimits } from "@/lib/api";
import type { IngestFlow } from "@/lib/hooks/useIngestFlow";
import { cx } from "@/lib/cx";
import { ChoiceField } from "@/components/forms/ChoiceField";
import { NumberField } from "@/components/forms/NumberField";
import { SlugField } from "@/components/forms/SlugField";
import { MechanismsPanel } from "@/components/dashboard/control/MechanismsPanel";
import { RunSummaryItem } from "@/components/chat/RunCard";
import { ColumnMappingPicker } from "./ColumnMappingPicker";
import { DatasetPreview } from "./DatasetPreview";
import { ComposerTools } from "./ComposerTools";
import { PipelineSetupSection } from "./PipelineSetupSection";
import { OptimizerSetupSection } from "./OptimizerSetupSection";
import { PipelineDependencies } from "./PipelineDependencies";
import { OriginCheckinPanel } from "./OriginCheckinPanel";
import { DatasetPickList } from "./DatasetPickList";

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

// Sub-pixel heights and a half-drawn row must not read as the reader having scrolled away.
const FOLLOW_SLACK_PX = 24;

// The one ingest conversation, hosted only by the chat tab; the "New campaign" modal hands
// the shared thread over here the moment a pick or drop advances it.
export function IngestConversation({
  flow,
  origins,
  datasets,
  liveSegment,
  runCard,
}: {
  flow: IngestFlow;
  origins?: OriginEntry[];
  datasets?: DatasetIndexEntry[];
  liveSegment?: ReactNode;
  // LAST in the thread; separate from the append-only `liveSegment` because it is always-current.
  runCard?: ReactNode;
}) {
  const { phase, messages } = flow;
  const [dragging, setDragging] = useState(false);
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  const showEntryList = phase.stage === "idle" && datasets !== undefined;
  // No state behind the fold: React writes `open` only when the PROP changes, so a poll tick
  // leaves a hand-opened list alone.
  const threadHasContent = messages.length > 0 || !!liveSegment || !!runCard;

  const threadRef = useRef<HTMLDivElement | null>(null);
  // A ref, not state: scrolling must not itself cause a render.
  const followRef = useRef(true);
  // No deps: growth arrives as `liveSegment` / `runCard` elements no dependency list can compare.
  useEffect(() => {
    const el = threadRef.current;
    if (el && followRef.current) el.scrollTop = el.scrollHeight;
  });

  return (
    <div className="ingest-conversation">
      <div
        className="chat-messages"
        aria-live="polite"
        ref={threadRef}
        onScroll={(e) => {
          const el = e.currentTarget;
          followRef.current =
            el.scrollHeight - el.scrollTop - el.clientHeight <= FOLLOW_SLACK_PX;
        }}
      >
        {showEntryList ? (
          <details
            className="new-campaign-optional"
            open={!threadHasContent}
            // The list expands ABOVE the tail; a still-following thread would scroll past it.
            onToggle={(e) => {
              if (e.currentTarget.open) followRef.current = false;
            }}
          >
            <summary>
              Start a campaign — {origins?.length ?? 0} origins · {datasets!.length} datasets
            </summary>
            <DatasetPickList
              origins={origins ?? []}
              datasets={datasets!}
              onOpenOrigin={flow.openOrigin}
              onPick={flow.pickDataset}
              busy={flow.busy}
            />
          </details>
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
          ) : msg.kind === "run" ? (
            <RunSummaryItem key={msg.id} summary={msg.summary} />
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

        {liveSegment}
        {runCard}
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
        <div className="chat-field">
          <textarea
            className="chat-input"
            // Must fit ONE line beside attach and send at 390px; formats live in `accept`.
            placeholder={flow.awaitingContext ? "Describe the task…" : "Drop a dataset file…"}
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
          <ComposerTools />
        </div>
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

// Held as TEXT: a half-typed "0." is not a number. Blank means "no cap of mine".
interface CapDrafts {
  halt: string;
  usd: string;
  tokens: string;
}
const NO_CAPS: CapDrafts = { halt: "", usd: "", tokens: "" };

// Anything finite is SENT, out-of-range too: `StartCheckinPayload` owns the bounds, and its 422
// names the field where a silently dropped key would not.
function launchLimits(c: CapDrafts): StartCheckinLimits {
  const num = (s: string) => {
    const n = Number(s);
    return s.trim() !== "" && Number.isFinite(n) ? n : undefined;
  };
  return {
    halt_at_accuracy: num(c.halt),
    spend_budget_usd: num(c.usd),
    token_budget: num(c.tokens),
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

      <OptimizerSetupSection />

      <details className="new-campaign-optional ingest-advanced">
        {/* Two persistence classes on purpose: the knobs patch the draft's `OptimizationConfig`,
            the caps ride the Start press and are saved nowhere. */}
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
          <ChoiceField
            label="Prompt block library"
            value={draft.optimization_overrides.prompt_block_catalogue}
            options={[
              { value: "guidance", label: "Suggest (reuse or invent)" },
              { value: "restrict", label: "Library only" },
              { value: "off", label: "Off" },
            ]}
            hint="Proven persona / thinking-style / answer-format blocks the optimizer can draw on."
            onApply={(prompt_block_catalogue) =>
              flow.applyPatch({ optimization_overrides: { prompt_block_catalogue } })
            }
          />
          <ChoiceField
            label="Escalation ladder"
            value={draft.optimization_overrides.escalation_ladder}
            options={[
              { value: "full", label: "Full (L1 → L2 → L3)" },
              { value: "l1_l2", label: "L1 + L2 (no replan)" },
              { value: "l1", label: "L1 only" },
            ]}
            hint="How far the loop may escalate when L1 stalls. L1 only never composes an L2 or L3 prompt — the ablation arm."
            onApply={(escalation_ladder) =>
              flow.applyPatch({ optimization_overrides: { escalation_ladder } })
            }
          />
          <MechanismsPanel
            mechanisms={draft.optimization_overrides.mechanisms}
            onChange={(mechanisms) =>
              flow.applyPatch({ optimization_overrides: { mechanisms } })
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

      {/* `saving` never disables Start: `startFromReady` awaits the in-flight edit, and
          disabling would eat the tap whose blur committed the field. */}
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
