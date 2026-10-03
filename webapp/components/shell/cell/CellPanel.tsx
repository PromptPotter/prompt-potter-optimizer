"use client";
import { useState } from "react";
import { fetchCell, type Cell, type CellSpan } from "@/lib/api";
import type { CellAddress } from "@/lib/address";
import { fmtPct0 } from "@/lib/format";
import { fitnessStyle } from "@/lib/derivations";
import { useRead } from "@/lib/hooks/useRead";
import { cx } from "@/lib/cx";
import { CopyButton, ErrorNote, Loading, SegmentedControl, SidePanel } from "@/components/ui";

// One measured cell opened. Chrome, so IDENTITY props only, never where to read from (I9);
// nothing is computed — the input is the server's re-render of what was sent.

type SpanTab = "input" | "output" | "details" | "tokens";

const SPAN_TABS = [
  { value: "input", label: "Input" },
  { value: "output", label: "Output" },
  { value: "details", label: "Details" },
  { value: "tokens", label: "Tokens" },
] as const;

function show(v: unknown): string {
  if (v == null) return "—";
  return typeof v === "string" ? v : JSON.stringify(v, null, 2);
}

function secs(v: number | null | undefined): string {
  return v == null ? "—" : `${v.toFixed(v < 10 ? 2 : 1)} s`;
}

function SpanBody({ span, tab }: { span: CellSpan; tab: SpanTab }) {
  if (tab === "input")
    return <pre className="cell-pre">{span.input ?? "No prompt configured on this node."}</pre>;
  if (tab === "output")
    return (
      <pre className="cell-pre">
        {Object.keys(span.outputs).length ? show(span.outputs) : "No output declared for this node."}
      </pre>
    );
  if (tab === "details")
    return (
      <dl className="cell-facts">
        <dt>Model</dt>
        <dd>{span.model ?? "—"}</dd>
        <dt>Provider</dt>
        <dd>{span.provider ?? "—"}</dd>
        <dt>Seconds</dt>
        <dd>{secs(span.seconds)}</dd>
        <dt>Config</dt>
        <dd>
          <pre className="cell-pre">{show(span.config)}</pre>
        </dd>
      </dl>
    );
  return (
    <dl className="cell-facts">
      <dt>Input tokens</dt>
      <dd>{span.input_tokens ?? "—"}</dd>
      <dt>Output tokens</dt>
      <dd>{span.output_tokens ?? "—"}</dd>
      <dt>Provider cache</dt>
      <dd>{span.cache_read_tokens ?? "—"}</dd>
      <dt>Cost</dt>
      <dd>{span.cost_usd == null ? "—" : `$${span.cost_usd.toFixed(5)}`}</dd>
      {span.estimated && (
        <>
          <dt>Counted</dt>
          <dd>estimated (chars/4) — the provider reported no usage</dd>
        </>
      )}
    </dl>
  );
}

function CellBody({ cell }: { cell: Cell }) {
  const [spanIdx, setSpanIdx] = useState(0);
  const [tab, setTab] = useState<SpanTab>("input");
  const span = cell.spans[spanIdx];
  return (
    <div className="cell-body">
      <div className="cell-summary">
        <span className={cx("cell-status", `is-${cell.status.toLowerCase()}`)}>{cell.status}</span>
        {cell.fitness != null && (
          <span className="cell-fit" style={fitnessStyle(cell.fitness)}>
            {fmtPct0(cell.fitness)}
          </span>
        )}
        {cell.cached && <span className="cell-tag">replayed</span>}
        <span className="cell-dim">{secs(cell.seconds)}</span>
        <span className="cell-dim">{cell.run_name}</span>
        <CopyButton data={cell} title="Copy this cell as JSON" />
      </div>
      <div className="cell-answer">
        <div>
          <div className="cell-label">Predicted</div>
          <pre className="cell-pre">{cell.predicted || "—"}</pre>
        </div>
        <div>
          <div className="cell-label">Ground truth</div>
          <pre className="cell-pre">{cell.ground_truth || "verifier-graded — no label"}</pre>
        </div>
      </div>
      {cell.error && <ErrorNote>{cell.error}</ErrorNote>}
      <details className="cell-query">
        <summary className="cell-label">Query</summary>
        <pre className="cell-pre">{cell.query}</pre>
      </details>
      {span && (
        <div className="cell-trace">
          <ol className="cell-spans" aria-label="Pipeline nodes">
            {cell.spans.map((sp, i) => (
              <li key={sp.node}>
                <button
                  type="button"
                  className={cx("cell-span", i === spanIdx && "on", sp.node === cell.terminal_node && "terminal")}
                  aria-pressed={i === spanIdx}
                  onClick={() => setSpanIdx(i)}
                >
                  <span className="cell-span-name">{sp.node}</span>
                  <span className="cell-dim">
                    {sp.model ?? "no model"} · {secs(sp.seconds)}
                  </span>
                </button>
              </li>
            ))}
          </ol>
          <div className="cell-inspect">
            <SegmentedControl ariaLabel="Span view" value={tab} onChange={setTab} options={SPAN_TABS} />
            <SpanBody span={span} tab={tab} />
          </div>
        </div>
      )}
      {Object.keys(cell.other_outputs).length > 0 && (
        <details className="cell-query">
          <summary className="cell-label">Everything else the row banked</summary>
          <pre className="cell-pre">{show(cell.other_outputs)}</pre>
        </details>
      )}
    </div>
  );
}

export function CellPanel({
  datasetName,
  cell,
  onClose,
  hasPrev,
  hasNext,
  onStep,
}: {
  datasetName: string;
  cell: CellAddress;
  onClose: () => void;
  hasPrev: boolean;
  hasNext: boolean;
  onStep: (shift: -1 | 1) => void;
}) {
  const read = useRead(
    {
      key: `${datasetName}/${cell.runId}/${cell.sampleId}`,
      fetch: (signal) => fetchCell(datasetName, cell.runId, cell.sampleId, signal),
    },
    { surface: "cell-panel" },
  );
  return (
    <SidePanel
      panelId="cell"
      title={`Sample ${cell.sampleId}`}
      onClose={onClose}
      hasPrev={hasPrev}
      hasNext={hasNext}
      onStep={onStep}
    >
      {read.status === "ready" ? (
        // Keyed by the address, so a J/K step starts on the first span again.
        <CellBody key={`${cell.runId}/${cell.sampleId}`} cell={read.data} />
      ) : read.status === "failed" ? (
        <ErrorNote>{read.failure.message}</ErrorNote>
      ) : (
        <Loading />
      )}
    </SidePanel>
  );
}
