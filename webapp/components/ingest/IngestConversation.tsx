"use client";

import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import type { DatasetIndexEntry, OriginEntry } from "@/lib/api";
import type { IngestFlow } from "@/lib/hooks/useIngestFlow";
import { cx } from "@/lib/cx";
import { ChoiceField } from "@/components/forms/ChoiceField";
import { NumberField } from "@/components/forms/NumberField";
import { SlugField } from "@/components/forms/SlugField";
import { MechanismsPanel } from "@/components/dashboard/control/MechanismsPanel";
import { RunSummaryItem } from "@/components/chat/RunCard";
import { ColumnMappingPicker } from "./ColumnMappingPicker";
import { ComposerTools } from "./ComposerTools";
import { PipelineSetupSection } from "./PipelineSetupSection";
import { OptimizerSetupSection } from "./OptimizerSetupSection";
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

// How far off the bottom still counts as being AT the tail. Sub-pixel scroll
// heights and a half-drawn row must not read as the reader having scrolled away.
const FOLLOW_SLACK_PX = 24;

// The single ingest conversation, driven by the one shared `useIngestFlow`
// (`lib/ingest-flow.tsx`). The chat tab hosts it; the "New campaign" modal shows
// the same resting entry list and hands the thread over the moment a pick or a
// drop advances it, so the conversation only ever happens in one place.
// One thread: pick/drop → ask context only if missing → one check-in → Start →
// then the live cycle's curated activity + decisions (`liveSegment`).
export function IngestConversation({
  flow,
  origins,
  datasets,
  liveSegment,
  runCard,
}: {
  flow: IngestFlow;
  // The entry lists: existing origins to reuse + datasets to make a new origin
  // from. Both surfaces supply them — withholding them from the chat tab is
  // what left a visitor with no file and no way in.
  origins?: OriginEntry[];
  datasets?: DatasetIndexEntry[];
  // The live tail (curated activity feed + decision buttons) appended into the
  // thread once a cycle is bound. Present only on the chat tab.
  liveSegment?: ReactNode;
  // The run card — LAST in the thread. Kept a separate slot from `liveSegment`:
  // that one is the append-only activity history, this one is a single
  // always-current pane.
  runCard?: ReactNode;
}) {
  const { phase, messages } = flow;
  const [dragging, setDragging] = useState(false);
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  // The resting state of either surface: what you can start from. Gated on the
  // collection having arrived, never on which pane is hosting the thread.
  const showEntryList = phase.stage === "idle" && datasets !== undefined;
  // Open on an empty thread — it is the only thing to do — and folded to its summary
  // once the thread has anything of its own, because a wall of every origin and dataset
  // is otherwise the tallest thing above the conversation. No state behind it: React
  // writes `open` only when the PROP changes, so a poll tick leaves a hand-opened list
  // alone, and the flip re-asserts exactly when the thread gains or loses content.
  const threadHasContent = messages.length > 0 || !!liveSegment || !!runCard;

  // Follow the tail. Nothing in the thread is pinned, so a live run would otherwise
  // append its newest step below the fold and leave the reader watching a stale
  // frame. Re-run on EVERY render because the growth arrives as `liveSegment` /
  // `runCard` elements, which no dependency list can compare.
  const threadRef = useRef<HTMLDivElement | null>(null);
  // A ref, not state: scrolling must not itself cause a render. Scrolling UP to read
  // is deliberate and the next poll tick must not undo it; scrolling back to the
  // bottom re-engages, which is the whole of the contract.
  const followRef = useRef(true);
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
        {/* FIRST in the thread, and inside the scroller with it: it scrolls away with
            everything else instead of holding a slot above the conversation. */}
        {showEntryList ? (
          <details
            className="new-campaign-optional"
            open={!threadHasContent}
            // The list expands ABOVE the tail, so a still-following thread would scroll
            // straight past what was just opened. Opening it is a read, and a read wins.
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

        {/* The live cycle's curated activity + inline decisions — the same
            ordered thread, continued. */}
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
        {/* The field carries the frame; the textarea inside it is bare. Tools sits in
            the field's own bottom-right corner rather than beside it — it names what this
            chat can do, and a label does not deserve a slot in the control row. */}
        <div className="chat-field">
          <textarea
            className="chat-input"
            // Short enough to sit on ONE line beside the attach and send buttons at
            // 390px — a composer that wraps to two rows is not a composer. The accepted
            // formats are the attach button's `accept` list and the pick-list's own copy;
            // spelling them here made this the widest thing in the row.
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

// The ready state: confirm the origin before Start. Any remaining gaps surface
// inline (check-in assessment + questions, column mapping). The starting prompt
// and pipeline are shown expanded + prefilled for confirmation — they're the
// origin the operator is about to evolve. Only the meta-optimizer knobs (which
// model drives the search, run bounds) collapse into an optional expander.
function ReadyBlock({ flow }: { flow: IngestFlow }) {
  const blockersId = useId();
  if (flow.phase.stage !== "ready") return null;
  const { draft, resolution, raised, degradedCause } = flow.phase;
  // `blocked` mirrors the server gate alone — adding `gaps.length` is a second
  // definition whose divergent state is a dead Start with no explanation.
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

      <ColumnMappingPicker draft={draft} onApply={flow.applyPatch} />

      {/* The campaign's NAME — an identity fact, so it sits with the other things that
          say what this run is. It used to be folded in with the loop knobs below, which
          is the one place a reader would never look for it. */}
      <SlugField slug={draft.slug} onApply={(slug) => flow.applyPatch({ slug })} />

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

      {/* The loop that will do the searching, drawn the way the chat hero draws it.
          Always open: it is what the operator is about to spend money running, and it
          used to reach this surface only as the round-ceiling number field below. */}
      <OptimizerSetupSection />

      <details className="new-campaign-optional ingest-advanced">
        {/* Bounds on the LOOP, not the optimizer's wiring — that is the section above.
            Both knobs are campaign policy (`OptimizationConfig`), which is why they are
            here and not on a node. */}
        <summary>Loop bounds (optional)</summary>
        <div className="new-campaign-optional-body">
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

      {/* The server gate's open fields, rendered against the button they close —
          a disabled button carries no tooltip on touch, so the reason must be
          text on the page. */}
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

      {/* `saving` never disables Start — `startFromReady` awaits the in-flight
          edit, and disabling would eat the tap whose blur committed the field. */}
      <button
        type="button"
        className="chat-cta-btn"
        disabled={blocked || flow.busy}
        aria-describedby={blocked ? blockersId : undefined}
        onClick={flow.startFromReady}
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
