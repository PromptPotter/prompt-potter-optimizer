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
  type DatasetIndexEntry,
  type DraftCampaignWire,
  type DraftPatch,
  type LLMProvider,
  type OriginGap,
  type ProvenanceTag,
} from "@/lib/api";
import { originReadiness, plainLanguageRecap } from "@/lib/origin-readiness";

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
  const benchmarkEntries =
    list.kind === "ready" ? list.entries.filter((d) => d.tier === "benchmark") : [];

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

  return (
    <div className="new-campaign-body">
      <p>
        Parsed <strong>{draft.n_samples}</strong> rows from{" "}
        <code>{draft.slug}</code>. First, map your columns — then tune the
        optional settings.
      </p>

      {/* Required tier — the only fields the mint gate blocks on today. */}
      <ColumnMappingPicker draft={draft} onApply={applyPatch} />

      {readiness.complete ? (
        <RecapCard text={plainLanguageRecap(draft)} />
      ) : (
        <GapList gaps={readiness.gaps} tone="pending" />
      )}

      {/* Optional tier — refine but never block. Collapsed so the required
          path stays short (origin-resolution spec § Operator surface). */}
      <details className="new-campaign-optional">
        <summary>Optional settings</summary>
        <div className="new-campaign-optional-body">
          <SlugField slug={draft.slug} onApply={(slug) => applyPatch({ slug })} />
          <TextField
            label="Task description"
            value={draft.task_description}
            placeholder="What is the model supposed to do with each row?"
            onApply={(task_description) => applyPatch({ task_description })}
          />
          <NumberField
            label="Max rounds"
            value={draft.max_rounds}
            min={1}
            max={100}
            onApply={(max_rounds) => applyPatch({ max_rounds })}
          />
          <OptimizerLLMField
            provider={draft.optimizer_provider}
            model={draft.optimizer_model}
            onApply={(optimizer_provider, optimizer_model) =>
              applyPatch({ optimizer_provider, optimizer_model })
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
          disabled={submitting || !readiness.complete}
          onClick={handleCommit}
        >
          {submitting ? "Creating…" : "Create campaign"}
        </button>
      </footer>
    </div>
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
  const sameColumn =
    !!draft.column_query && draft.column_query === draft.column_ground_truth;

  return (
    <div className="origin-columns">
      <span className="origin-columns-head">Map your columns</span>
      <div className="origin-column-row">
        <label className="new-campaign-field">
          <span>
            Input column <ProvenanceBadge tag={queryProv} />
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
