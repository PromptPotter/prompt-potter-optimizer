"use client";
// IngestPane — the M13 chat-first "New campaign" surface.
//
// Two-mode entry against the identity-scoped `GET /datasets`:
//   * empty `tier: "yours"` → drop a CSV, see preview, tune defaults, commit
//   * non-empty → list the collection, pick an Origin, mint a campaign
//
// Wire contract: `docs/specs/m12-api-openapi.yaml`
//   POST /datasets/ingest                       (upload → DraftCampaign)
//   POST /commands/edit-draft-campaign          (sparse-patch via panel/chat)
//   POST /commands/mint-campaign-from-draft     (commit + spawn runner)
//   POST /commands/mint-campaign                (existing Origin → runner)
//
// Per `docs/specs/m13-chat-first-user-web.md § Draft-campaign object`, the
// chat surface and the panel are two views over the same server-held
// `DraftCampaign`. Slice 1 ships the panel-side; the chat tool-call surface
// reuses the same `postEditDraftCampaign` verb.

import { useEffect, useRef, useState } from "react";
import {
  fetchDatasetIndex,
  fetchLLMProviders,
  IngestApiError,
  postEditDraftCampaign,
  postIngestDataset,
  postMintCampaign,
  postMintCampaignFromDraft,
  postResolveOrigin,
  type DatasetIndexEntry,
  type DraftCampaignWire,
  type DraftPatch,
  type LLMProvider,
  type OriginGap,
  type OriginLastResolution,
  type OriginQuestion,
  type ProvenanceSource,
  type ProvenanceTag,
} from "@/lib/api";
import {
  originReadiness,
  plainLanguageRecap,
  questionOptions,
  questionPatch,
} from "@/lib/origin-readiness";
import {
  lockedParams,
  primaryNode,
  thinkingLadder,
  type ParamLock,
} from "@/lib/optimizer-locks";

interface Props {
  open: boolean;
  onClose: () => void;
  onMinted?: (campaignId: string, cycleId: string) => void;
}

type LoadState =
  | { kind: "loading" }
  | { kind: "ready"; entries: DatasetIndexEntry[] }
  | { kind: "error"; message: string };

export function IngestPane({ open, onClose, onMinted }: Props) {
  // Render-phase guarded reset on `open` toggles. Async fetch fires from a
  // bare effect that never synchronously setStates.
  const [prevOpen, setPrevOpen] = useState(open);
  const [list, setList] = useState<LoadState>({ kind: "loading" });
  if (open !== prevOpen) {
    setPrevOpen(open);
    if (open) setList({ kind: "loading" });
  }

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    fetchDatasetIndex()
      .then((r) => {
        if (!cancelled) setList({ kind: "ready", entries: r.datasets });
      })
      .catch((e) => {
        if (!cancelled) {
          setList({
            kind: "error",
            message: e instanceof Error ? e.message : String(e),
          });
        }
      });
    return () => {
      cancelled = true;
    };
  }, [open]);

  if (!open) return null;

  const ownedEntries =
    list.kind === "ready" ? list.entries.filter((d) => d.tier === "yours") : [];
  // The try-and-learn demo rides the same "ready-to-run" bucket as benchmarks.
  const benchmarkEntries =
    list.kind === "ready"
      ? list.entries.filter((d) => d.tier === "benchmark" || d.tier === "demo")
      : [];

  return (
    <div
      className="new-campaign-overlay"
      role="dialog"
      aria-modal="true"
      aria-label="New campaign"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="new-campaign-modal">
        <header className="new-campaign-header">
          <h2>New campaign</h2>
          <button
            type="button"
            className="new-campaign-close"
            aria-label="Close"
            onClick={onClose}
          >
            ×
          </button>
        </header>
        {list.kind === "loading" ? (
          <div className="new-campaign-body">
            <em className="new-campaign-loading">Loading your collection…</em>
          </div>
        ) : list.kind === "error" ? (
          <div className="new-campaign-body">
            <p className="new-campaign-error">{list.message}</p>
          </div>
        ) : ownedEntries.length === 0 ? (
          <ChatIngestFlow
            benchmarks={benchmarkEntries}
            onClose={onClose}
            onMinted={onMinted}
          />
        ) : (
          <ListAndMintFlow
            owned={ownedEntries}
            benchmarks={benchmarkEntries}
            onClose={onClose}
            onMinted={onMinted}
            onAddOrigin={() => setList({ kind: "ready", entries: benchmarkEntries })}
          />
        )}
      </div>
    </div>
  );
}

// ----- Empty-collection branch — drop a CSV, tune, commit -------------------

function ChatIngestFlow({
  benchmarks,
  onClose,
  onMinted,
}: {
  benchmarks: DatasetIndexEntry[];
  onClose: () => void;
  onMinted?: (campaignId: string, cycleId: string) => void;
}) {
  const [draft, setDraft] = useState<DraftCampaignWire | null>(null);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const onFileDrop = async (file: File) => {
    setUploadError(null);
    setBusy(true);
    try {
      const d = await postIngestDataset(file);
      setDraft(d);
    } catch (e) {
      setUploadError(IngestApiError.toOperatorMessage(e));
    } finally {
      setBusy(false);
    }
  };

  if (draft === null) {
    return (
      <div className="new-campaign-body">
        <p>
          Drop a CSV to start your first Origin — any column names work.
          You&rsquo;ll pick which column is the input and which is the target
          on the next step.
        </p>
        <FileDropZone busy={busy} onFile={onFileDrop} />
        {uploadError ? <p className="new-campaign-error">{uploadError}</p> : null}
        {benchmarks.length > 0 ? (
          <details>
            <summary>Or run a benchmark</summary>
            <BenchmarkList
              benchmarks={benchmarks}
              onClose={onClose}
              onMinted={onMinted}
            />
          </details>
        ) : null}
      </div>
    );
  }

  return (
    <DraftCommitFlow
      draft={draft}
      onDraftChange={setDraft}
      onClose={onClose}
      onMinted={onMinted}
    />
  );
}

// ----- Non-empty branch — list collection, pick one, mint -------------------

function ListAndMintFlow({
  owned,
  benchmarks,
  onClose,
  onMinted,
  onAddOrigin,
}: {
  owned: DatasetIndexEntry[];
  benchmarks: DatasetIndexEntry[];
  onClose: () => void;
  onMinted?: (campaignId: string, cycleId: string) => void;
  onAddOrigin: () => void;
}) {
  const [picked, setPicked] = useState<string>(owned[0]?.name ?? "");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleMint = async () => {
    if (!picked) return;
    setSubmitting(true);
    setError(null);
    try {
      await postMintCampaign(picked);
      onMinted?.("", "");
      onClose();
    } catch (e) {
      setError(IngestApiError.toOperatorMessage(e));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="new-campaign-body">
      <label className="new-campaign-field">
        <span>Origin</span>
        <select value={picked} onChange={(e) => setPicked(e.target.value)} required>
          {owned.map((d) => (
            <option key={d.name} value={d.name}>
              {d.title ? `${d.name} — ${d.title}` : d.name} · {d.n_samples} samples
            </option>
          ))}
          {benchmarks.length > 0 ? (
            <optgroup label="Benchmarks">
              {benchmarks.map((d) => (
                <option key={d.name} value={d.name}>
                  {d.title ? `${d.name} — ${d.title}` : d.name} · {d.n_samples} samples
                </option>
              ))}
            </optgroup>
          ) : null}
        </select>
      </label>
      {error ? <p className="new-campaign-error">{error}</p> : null}
      <footer className="new-campaign-footer">
        <button type="button" className="new-campaign-cancel" onClick={onAddOrigin}>
          Add an Origin
        </button>
        <button
          type="button"
          className="new-campaign-submit"
          disabled={submitting || !picked}
          onClick={handleMint}
        >
          {submitting ? "Starting…" : "Start campaign"}
        </button>
      </footer>
    </div>
  );
}

function BenchmarkList({
  benchmarks,
  onClose,
  onMinted,
}: {
  benchmarks: DatasetIndexEntry[];
  onClose: () => void;
  onMinted?: (campaignId: string, cycleId: string) => void;
}) {
  const [submitting, setSubmitting] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const mint = async (name: string) => {
    setSubmitting(name);
    setError(null);
    try {
      await postMintCampaign(name);
      onMinted?.("", "");
      onClose();
    } catch (e) {
      setError(IngestApiError.toOperatorMessage(e));
    } finally {
      setSubmitting(null);
    }
  };

  return (
    <>
      <ul className="new-campaign-benchmark-list">
        {benchmarks.map((d) => (
          <li key={d.name}>
            <button
              type="button"
              disabled={submitting !== null}
              onClick={() => void mint(d.name)}
            >
              {submitting === d.name
                ? "Starting…"
                : d.title
                  ? `${d.name} — ${d.title}`
                  : d.name}
            </button>
          </li>
        ))}
      </ul>
      {error ? <p className="new-campaign-error">{error}</p> : null}
    </>
  );
}

// ----- Draft commit (used by ChatIngestFlow after a successful upload) -----

function DraftCommitFlow({
  draft,
  onDraftChange,
  onClose,
  onMinted,
}: {
  draft: DraftCampaignWire;
  onDraftChange: (d: DraftCampaignWire) => void;
  onClose: () => void;
  onMinted?: (campaignId: string, cycleId: string) => void;
}) {
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Server-side `origin_incomplete` gaps from a rejected mint. Distinct from
  // the proactively-derived gaps shown in the required tier: these only
  // appear if the operator forces a commit the client gate already blocks.
  const [serverGaps, setServerGaps] = useState<OriginGap[] | null>(null);
  // One origin-resolver turn in flight + its last output (assessment,
  // operator questions, ready-turn recap).
  const [resolving, setResolving] = useState(false);
  const [lastResolution, setLastResolution] = useState<OriginLastResolution | null>(null);

  const readiness = originReadiness(draft);

  const applyPatch = async (patch: DraftPatch) => {
    setError(null);
    setServerGaps(null);
    try {
      const next = await postEditDraftCampaign(draft.draft_id, patch);
      onDraftChange(next);
    } catch (e) {
      setError(IngestApiError.toOperatorMessage(e));
    }
  };

  const handleResolve = async () => {
    setResolving(true);
    setError(null);
    setServerGaps(null);
    try {
      const r = await postResolveOrigin(draft.draft_id);
      onDraftChange(r.draft);
      setLastResolution(r.resolution.last_resolution ?? null);
    } catch (e) {
      setError(IngestApiError.toOperatorMessage(e));
    } finally {
      setResolving(false);
    }
  };

  const handleCommit = async () => {
    setSubmitting(true);
    setError(null);
    setServerGaps(null);
    try {
      const r = await postMintCampaignFromDraft(draft.draft_id);
      onMinted?.(r.campaign_id, r.cycle_id);
      onClose();
    } catch (e) {
      if (e instanceof IngestApiError && e.errorCode === "origin_incomplete" && e.gaps) {
        setServerGaps(e.gaps);
      }
      setError(IngestApiError.toOperatorMessage(e));
    } finally {
      setSubmitting(false);
    }
  };

  // Two phases over one server-held draft: the origin setup (map columns + AI
  // check-in) gates on `readiness.complete`; once ready, the permission step
  // (pipeline + optimizer locks) is the "another window" before mint.
  if (!readiness.complete) {
    return (
      <div className="new-campaign-body">
        <p>
          Parsed <strong>{draft.n_samples}</strong> rows from{" "}
          <code>{draft.slug}</code>. First, map your columns — then let the
          check-in finish the setup.
        </p>

        {/* Required tier — the column mapping the mint gate blocks on. */}
        <ColumnMappingPicker draft={draft} onApply={applyPatch} />

        {/* Origin setup-in-progress — the closed-set fields with provenance, and
            the AI resolver that proposes the task framing + refinements. */}
        <OriginCheckinPanel
          draft={draft}
          resolving={resolving}
          lastResolution={lastResolution}
          onResolve={handleResolve}
          onApply={applyPatch}
        />

        {/* Task framing is the one required field with no default — let the
            operator type it directly, not only via "Set up with AI". */}
        <TextField
          label="Describe what the prompt should do"
          value={draft.task_description}
          placeholder="e.g. Classify each support ticket into one category."
          onApply={(task_description) => applyPatch({ task_description })}
        />

        <GapList gaps={readiness.gaps} tone="pending" />

        {error ? <p className="new-campaign-error">{error}</p> : null}
        <footer className="new-campaign-footer">
          <button type="button" className="new-campaign-cancel" onClick={onClose}>
            Cancel
          </button>
        </footer>
      </div>
    );
  }

  return (
    <PermissionReviewStep
      draft={draft}
      recapText={lastResolution?.recap || plainLanguageRecap(draft)}
      submitting={submitting}
      serverGaps={serverGaps}
      error={error}
      onApply={applyPatch}
      onStart={handleCommit}
      onClose={onClose}
    />
  );
}

// ----- Phase 2: pipeline & optimizer-permission review (pre-mint) -----------

// The "another window" before mint. Setup is ready; this surfaces what the
// optimizer may and may NOT permute on the backend pipeline — the locked model,
// the thinking floor (`low`) with `medium`/`high` crossed out — so the operator
// starts knowing the rails, or opens "Review hyperparameters" to see the full
// per-node lock list (+ the optional settings) inline.
function PermissionReviewStep({
  draft,
  recapText,
  submitting,
  serverGaps,
  error,
  onApply,
  onStart,
  onClose,
}: {
  draft: DraftCampaignWire;
  recapText: string;
  submitting: boolean;
  serverGaps: OriginGap[] | null;
  error: string | null;
  onApply: (patch: DraftPatch) => void;
  onStart: () => void;
  onClose: () => void;
}) {
  const [reviewing, setReviewing] = useState(false);
  const locks = draft.optimizer_locks;
  const node = primaryNode(locks);
  const thinking = thinkingLadder(locks, node);
  const modelLocked = locks.forbidden_axes.includes("model");

  return (
    <div className="new-campaign-body">
      <RecapCard text={recapText} />

      <section className="opt-locks">
        <header className="origin-columns-head">Pipeline &amp; optimizer permissions</header>
        <p className="opt-locks-lead">
          The optimizer evolves your prompt — but it can&rsquo;t touch these. They
          stay fixed for the whole campaign.
        </p>

        <div className="opt-locks-row">
          <span className="opt-locks-label">Pipeline</span>
          <span className="opt-locks-pipeline">
            {locks.pipeline.length > 0
              ? locks.pipeline.map((step) => (
                  <span key={step} className="opt-locks-chip">
                    {step}
                  </span>
                ))
              : "backend default"}
          </span>
        </div>

        {modelLocked ? (
          <div className="opt-locks-row">
            <span className="opt-locks-label">Model</span>
            <span className="opt-locks-locked">
              <span className="opt-locks-lock" aria-hidden="true">
                🔒
              </span>
              Locked — the optimizer can&rsquo;t change the model.
            </span>
          </div>
        ) : null}

        <div className="opt-locks-row">
          <span className="opt-locks-label">Thinking</span>
          <span className="opt-locks-ladder">
            {thinking.options.map((opt) => (
              <span
                key={opt.key}
                className={`opt-locks-level${opt.active ? " is-active" : ""}${
                  opt.allowed ? "" : " is-crossed"
                }`}
                title={
                  opt.allowed
                    ? opt.active
                      ? "Current level"
                      : "Allowed"
                    : "Optimizer locked out of this level"
                }
              >
                {opt.key}
              </span>
            ))}
          </span>
        </div>
        <small className="opt-locks-hint">
          Crossed-out levels are off-limits to the optimizer — it can&rsquo;t
          escalate thinking beyond <strong>{thinking.value ?? "the floor"}</strong>.
        </small>
      </section>

      <details
        className="new-campaign-optional"
        open={reviewing}
        onToggle={(e) => setReviewing((e.target as HTMLDetailsElement).open)}
      >
        <summary>Review hyperparameters</summary>
        <div className="new-campaign-optional-body">
          <LockTable params={lockedParams(locks)} />
          <OptionalSettings draft={draft} onApply={onApply} />
        </div>
      </details>

      {serverGaps ? <GapList gaps={serverGaps} tone="blocked" /> : null}
      {error ? <p className="new-campaign-error">{error}</p> : null}
      <footer className="new-campaign-footer">
        <button type="button" className="new-campaign-cancel" onClick={onClose}>
          Cancel
        </button>
        <button
          type="button"
          className="new-campaign-submit"
          disabled={submitting}
          onClick={onStart}
        >
          {submitting ? "Starting…" : "Start campaign"}
        </button>
      </footer>
    </div>
  );
}

// The full per-node lock list shown under "Review hyperparameters" — every
// param tagged locked (model/provider; constrained allow-sets) vs optimizer-free.
function LockTable({ params }: { params: ParamLock[] }) {
  if (params.length === 0) return null;
  const REASON_LABEL: Record<ParamLock["reason"], string> = {
    forbidden: "optimizer can't change this",
    constrained: "limited to an allowed set",
    free: "optimizer-free",
  };
  return (
    <ul className="opt-locks-table">
      {params.map((p) => (
        <li key={`${p.node}.${p.param}`} className="opt-locks-param">
          <span className="opt-locks-param-name">
            {p.node}.{p.param}
          </span>
          {p.value !== undefined ? (
            <code className="opt-locks-param-value">{String(p.value)}</code>
          ) : (
            <span className="opt-locks-param-value opt-locks-param-value--empty">—</span>
          )}
          <span
            className={`opt-locks-tag${p.locked ? " is-locked" : " is-free"}`}
            title={REASON_LABEL[p.reason]}
          >
            {p.locked ? "🔒 locked" : "✎ free"}
          </span>
        </li>
      ))}
    </ul>
  );
}

// The optional, never-blocking refinements — slug, task framing, max rounds,
// optimizer LLM, sample preview. Folded under the Review-hyperparameters
// expander so the start path stays short.
function OptionalSettings({
  draft,
  onApply,
}: {
  draft: DraftCampaignWire;
  onApply: (patch: DraftPatch) => void;
}) {
  return (
    <>
      <SlugField slug={draft.slug} onApply={(slug) => onApply({ slug })} />
      <TextField
        label="Task description"
        value={draft.task_description}
        placeholder="What is the model supposed to do with each row?"
        onApply={(task_description) => onApply({ task_description })}
      />
      <NumberField
        label="Max rounds"
        value={draft.max_rounds}
        min={1}
        max={100}
        onApply={(max_rounds) => onApply({ max_rounds })}
      />
      <OptimizerLLMField
        provider={draft.optimizer_provider}
        model={draft.optimizer_model}
        onApply={(optimizer_provider, optimizer_model) =>
          onApply({ optimizer_provider, optimizer_model })
        }
      />
      <details>
        <summary>Sample preview ({draft.sample_preview.length})</summary>
        <ul className="new-campaign-preview-list">
          {draft.sample_preview.map((row, i) => (
            <li key={i}>
              <code>{row.query}</code> → <code>{row.ground_truth}</code>
            </li>
          ))}
        </ul>
      </details>
    </>
  );
}

// ----- Origin-readiness surfaces (A3 gate consumption) ---------------------

const PROVENANCE_LABEL: Record<ProvenanceTag, string> = {
  unset: "Not set",
  proposed: "Proposed",
  confirmed: "Confirmed",
};

function ProvenanceBadge({ tag }: { tag: ProvenanceTag }) {
  return (
    <span className={`origin-prov origin-prov--${tag}`}>{PROVENANCE_LABEL[tag]}</span>
  );
}

// Plain-language source labels — `auto` is a smart default the operator can
// override; `stated` is a choice they made. Audit sub-tag, only shown once a
// field has a value (.impeccable register: no jargon, accessibility-first).
const SOURCE_LABEL: Record<ProvenanceSource, string> = {
  auto: "auto",
  stated: "you set",
};

function SourceBadge({ source }: { source: ProvenanceSource | undefined }) {
  if (!source) return null;
  return (
    <span className={`origin-src origin-src--${source}`}>{SOURCE_LABEL[source]}</span>
  );
}

// The closed-set config fields, surfaced so the once-hidden defaults are
// visible + their provenance honest. Each reads its tag from `draft.resolved`.
const CHECKIN_FIELDS: Array<{ key: string; label: string; value: (d: DraftCampaignWire) => string }> =
  [
    { key: "task_description", label: "Task", value: (d) => d.task_description || "—" },
    { key: "connector", label: "Backend", value: (d) => d.connector },
    { key: "scoring_composite", label: "Scoring", value: (d) => d.scoring_composite },
    { key: "max_rounds", label: "Max rounds", value: (d) => String(d.max_rounds) },
    {
      key: "optimizer.provider",
      label: "Optimizer",
      value: (d) => `${d.optimizer_provider}${d.optimizer_model ? ` · ${d.optimizer_model}` : ""}`,
    },
  ];

// The origin-setup-in-progress window. Shows the closed-set fields with their
// provenance, an "AI set-up" turn that proposes the task framing + refinements
// (origin-resolution steps 3-4), and the resolver's assessment / questions.
function OriginCheckinPanel({
  draft,
  resolving,
  lastResolution,
  onResolve,
  onApply,
}: {
  draft: DraftCampaignWire;
  resolving: boolean;
  lastResolution: OriginLastResolution | null;
  onResolve: () => void;
  onApply: (patch: DraftPatch) => void;
}) {
  const ready = CHECKIN_FIELDS.filter(
    (f) => (draft.resolved[f.key] ?? "unset") === "confirmed",
  ).length;
  const questions = lastResolution?.next_action.questions ?? [];

  return (
    <section className="origin-checkin">
      <header className="origin-checkin-head">
        <span className="origin-columns-head">Campaign setup</span>
        <span className="origin-checkin-progress">
          {ready}/{CHECKIN_FIELDS.length} ready
        </span>
      </header>

      <ul className="origin-checkin-fields">
        {CHECKIN_FIELDS.map((f) => {
          const tag: ProvenanceTag = draft.resolved[f.key] ?? "unset";
          const source: ProvenanceSource | undefined = draft.sources[f.key];
          return (
            <li key={f.key} className="origin-checkin-field">
              <span className="origin-checkin-label">{f.label}</span>
              <span className="origin-checkin-value">{f.value(draft)}</span>
              <ProvenanceBadge tag={tag} />
              <SourceBadge source={source} />
            </li>
          );
        })}
      </ul>

      <div className="origin-checkin-actions">
        <button
          type="button"
          className="origin-checkin-resolve"
          disabled={resolving}
          onClick={onResolve}
        >
          {resolving ? "Setting up…" : "Set up with AI"}
        </button>
        <small className="origin-checkin-hint">
          Reads your columns + sample rows to draft the task framing and propose
          any unset fields. High-confidence picks confirm automatically; the
          rest wait for you.
        </small>
      </div>

      {lastResolution?.assessment ? (
        <p className="origin-checkin-assessment">{lastResolution.assessment}</p>
      ) : null}
      {questions.length > 0 ? (
        <ul className="origin-checkin-questions">
          {questions.map((q, i) => (
            <li key={i}>
              <QuestionAnswer question={q} draft={draft} onApply={onApply} />
            </li>
          ))}
        </ul>
      ) : null}
    </section>
  );
}

// One answerable resolver question. Renders the prompt plus a control keyed to
// the field's answer set: a picker when the resolver gave `options` (or for a
// column-mapping question, the uploaded headers), else a free-text input.
// Submitting maps the answer to an `edit-draft-campaign` patch via
// `questionPatch` (server flips the field CONFIRMED + STATED) — the answer-back
// half of the resolver loop. A field that isn't string-applicable yields no
// patch and the control is omitted (only its prompt shows).
function QuestionAnswer({
  question,
  draft,
  onApply,
}: {
  question: OriginQuestion;
  draft: DraftCampaignWire;
  onApply: (patch: DraftPatch) => void;
}) {
  const [text, setText] = useState("");
  const options = questionOptions(question.field, question.options, draft.headers);
  // "1" is a valid probe across every mapped field (incl. max_rounds' numeric
  // guard); only backend.node_config / unknown fields yield null → not answerable.
  const answerable = questionPatch(question.field, "1") !== null;

  const submit = (answer: string) => {
    const patch = questionPatch(question.field, answer);
    if (patch) onApply(patch);
  };

  return (
    <div className="origin-question">
      <span className="origin-question-prompt">{question.prompt}</span>
      {!answerable ? null : options.length > 0 ? (
        <select
          className="origin-question-select"
          defaultValue=""
          onChange={(e) => submit(e.target.value)}
        >
          <option value="" disabled>
            — choose —
          </option>
          {options.map((opt) => (
            <option key={opt} value={opt}>
              {opt}
            </option>
          ))}
        </select>
      ) : (
        <span className="origin-question-text">
          <input
            type="text"
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder="Your answer"
          />
          <button type="button" disabled={!text.trim()} onClick={() => submit(text)}>
            Answer
          </button>
        </span>
      )}
    </div>
  );
}

// The required tier: pick which uploaded header is the input and which is the
// target. Selecting a column confirms it (rides `edit-draft-campaign` with
// `column_query` / `column_ground_truth`) — no separate Apply click, since
// the pick *is* the confirmation.
function ColumnMappingPicker({
  draft,
  onApply,
}: {
  draft: DraftCampaignWire;
  onApply: (patch: DraftPatch) => void;
}) {
  if (draft.headers.length === 0) {
    return (
      <p className="new-campaign-error">
        No columns were detected in the upload. Re-upload a CSV with a header row.
      </p>
    );
  }

  const queryProv: ProvenanceTag = draft.resolved["column.query"] ?? "unset";
  const gtProv: ProvenanceTag = draft.resolved["column.ground_truth"] ?? "unset";
  const querySrc: ProvenanceSource | undefined = draft.sources["column.query"];
  const gtSrc: ProvenanceSource | undefined = draft.sources["column.ground_truth"];
  const sameColumn =
    !!draft.column_query && draft.column_query === draft.column_ground_truth;

  return (
    <div className="origin-columns">
      <span className="origin-columns-head">Map your columns</span>
      <div className="origin-column-row">
        <label className="new-campaign-field">
          <span>
            Input column <ProvenanceBadge tag={queryProv} />
            <SourceBadge source={querySrc} />
          </span>
          <select
            value={draft.column_query}
            onChange={(e) => onApply({ column_query: e.target.value })}
          >
            <option value="" disabled>
              — pick a column —
            </option>
            {draft.headers.map((h) => (
              <option key={h} value={h}>
                {h}
              </option>
            ))}
          </select>
        </label>
        <label className="new-campaign-field">
          <span>
            Target column <ProvenanceBadge tag={gtProv} />
            <SourceBadge source={gtSrc} />
          </span>
          <select
            value={draft.column_ground_truth}
            onChange={(e) => onApply({ column_ground_truth: e.target.value })}
          >
            <option value="" disabled>
              — pick a column —
            </option>
            {draft.headers.map((h) => (
              <option key={h} value={h}>
                {h}
              </option>
            ))}
          </select>
        </label>
      </div>
      {sameColumn ? (
        <small className="new-campaign-warn">
          Input and target are the same column — every answer will trivially
          match. Pick two different columns.
        </small>
      ) : null}
    </div>
  );
}

function GapList({ gaps, tone }: { gaps: OriginGap[]; tone: "pending" | "blocked" }) {
  if (gaps.length === 0) return null;
  return (
    <ul className={`origin-gaps origin-gaps--${tone}`}>
      {gaps.map((g) => (
        <li key={g.field}>{g.hint}</li>
      ))}
    </ul>
  );
}

function RecapCard({ text }: { text: string }) {
  return (
    <div className="origin-recap" role="status">
      <span className="origin-recap-tick" aria-hidden="true">
        ✓
      </span>
      <p>{text}</p>
    </div>
  );
}

// ----- Field primitives ----------------------------------------------------

function SlugField({
  slug,
  onApply,
}: {
  slug: string;
  onApply: (slug: string) => void;
}) {
  const [prevSlug, setPrevSlug] = useState(slug);
  const [local, setLocal] = useState(slug);
  if (slug !== prevSlug) {
    setPrevSlug(slug);
    setLocal(slug);
  }
  return (
    <label className="new-campaign-field">
      <span>Slug</span>
      <span style={{ display: "flex", gap: "0.5rem" }}>
        <input
          type="text"
          value={local}
          onChange={(e) => setLocal(e.target.value)}
          pattern="^[a-z][a-z0-9_-]*$"
        />
        <button
          type="button"
          disabled={local === slug}
          onClick={() => onApply(local)}
        >
          Apply
        </button>
      </span>
    </label>
  );
}

function TextField({
  label,
  value,
  placeholder,
  onApply,
}: {
  label: string;
  value: string;
  placeholder?: string;
  onApply: (value: string) => void;
}) {
  const [prevValue, setPrevValue] = useState(value);
  const [local, setLocal] = useState(value);
  if (value !== prevValue) {
    setPrevValue(value);
    setLocal(value);
  }
  return (
    <label className="new-campaign-field">
      <span>{label}</span>
      <textarea
        value={local}
        placeholder={placeholder}
        onChange={(e) => setLocal(e.target.value)}
        rows={3}
      />
      <button type="button" disabled={local === value} onClick={() => onApply(local)}>
        Apply
      </button>
    </label>
  );
}

function NumberField({
  label,
  value,
  min,
  max,
  onApply,
}: {
  label: string;
  value: number;
  min?: number;
  max?: number;
  onApply: (value: number) => void;
}) {
  const [prevValue, setPrevValue] = useState(value);
  const [local, setLocal] = useState<string>(String(value));
  if (value !== prevValue) {
    setPrevValue(value);
    setLocal(String(value));
  }
  return (
    <label className="new-campaign-field">
      <span>{label}</span>
      <span style={{ display: "flex", gap: "0.5rem" }}>
        <input
          type="number"
          value={local}
          min={min}
          max={max}
          step={1}
          onChange={(e) => setLocal(e.target.value)}
        />
        <button
          type="button"
          disabled={local === String(value) || Number.isNaN(parseInt(local, 10))}
          onClick={() => onApply(parseInt(local, 10))}
        >
          Apply
        </button>
      </span>
    </label>
  );
}

// Optimizer-LLM picker. Fetches the curated provider list at mount and
// surfaces availability per provider — providers whose API key isn't
// configured render dimmed with the env-var name the operator needs to set.
// "" model means "use settings.LLM_MODEL fallback" — kept as an explicit
// option so the operator can pick "default" without typing.
function OptimizerLLMField({
  provider,
  model,
  onApply,
}: {
  provider: string;
  model: string;
  onApply: (provider: string, model: string) => void;
}) {
  const [providers, setProviders] = useState<LLMProvider[] | null>(null);
  const [localProvider, setLocalProvider] = useState(provider);
  const [localModel, setLocalModel] = useState(model);
  const [prevProvider, setPrevProvider] = useState(provider);
  const [prevModel, setPrevModel] = useState(model);
  if (provider !== prevProvider) {
    setPrevProvider(provider);
    setLocalProvider(provider);
  }
  if (model !== prevModel) {
    setPrevModel(model);
    setLocalModel(model);
  }

  useEffect(() => {
    let cancelled = false;
    fetchLLMProviders()
      .then((r) => {
        if (!cancelled) setProviders(r.providers);
      })
      .catch(() => {
        if (!cancelled) setProviders([]);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const selectedSpec = providers?.find((p) => p.name === localProvider);
  const dirty = localProvider !== provider || localModel !== model;
  const unavailable = selectedSpec ? !selectedSpec.available : false;

  return (
    <label className="new-campaign-field">
      <span>Optimizer LLM</span>
      <span style={{ display: "flex", flexDirection: "column", gap: "0.4rem" }}>
        <span style={{ display: "flex", gap: "0.5rem" }}>
          <select
            value={localProvider}
            onChange={(e) => {
              setLocalProvider(e.target.value);
              setLocalModel("");
            }}
          >
            {(providers ?? [{ name: provider, display_name: provider, available: true, env_var: "", models: [], note: "" }]).map((p) => (
              <option key={p.name} value={p.name}>
                {p.display_name}
                {p.available ? "" : ` (no ${p.env_var})`}
              </option>
            ))}
          </select>
          <select
            value={localModel}
            onChange={(e) => setLocalModel(e.target.value)}
            style={{ flex: 1 }}
          >
            <option value="">— provider default —</option>
            {(selectedSpec?.models ?? []).map((m) => (
              <option key={m} value={m}>
                {m}
              </option>
            ))}
            {localModel && !(selectedSpec?.models ?? []).includes(localModel) ? (
              <option value={localModel}>{localModel} (custom)</option>
            ) : null}
          </select>
          <button
            type="button"
            disabled={!dirty}
            onClick={() => onApply(localProvider, localModel)}
          >
            Apply
          </button>
        </span>
        {unavailable && selectedSpec ? (
          <small className="new-campaign-error">
            Set <code>{selectedSpec.env_var}</code> in <code>.env</code> before applying — the runner will crash at first call otherwise.
          </small>
        ) : selectedSpec?.note ? (
          <small style={{ color: "var(--color-text-tertiary)" }}>{selectedSpec.note}</small>
        ) : null}
      </span>
    </label>
  );
}

function FileDropZone({
  busy,
  onFile,
}: {
  busy: boolean;
  onFile: (file: File) => void;
}) {
  const [dragging, setDragging] = useState(false);
  const inputRef = useRef<HTMLInputElement | null>(null);

  return (
    <div
      className={`new-campaign-dropzone${dragging ? " new-campaign-dropzone--active" : ""}`}
      onDragOver={(e) => {
        e.preventDefault();
        setDragging(true);
      }}
      onDragLeave={() => setDragging(false)}
      onDrop={(e) => {
        e.preventDefault();
        setDragging(false);
        const file = e.dataTransfer.files?.[0];
        if (file) onFile(file);
      }}
      onClick={() => inputRef.current?.click()}
      role="button"
      tabIndex={0}
      aria-busy={busy}
    >
      <input
        ref={inputRef}
        type="file"
        accept=".csv,text/csv"
        hidden
        onChange={(e) => {
          const file = e.target.files?.[0];
          if (file) onFile(file);
          e.target.value = "";
        }}
      />
      {busy ? "Parsing…" : "Drop a CSV here or click to choose"}
    </div>
  );
}
