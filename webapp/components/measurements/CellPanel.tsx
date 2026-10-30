"use client";
import { useState } from "react";
import { cellRead, type Cell, type CellSpan } from "@/lib/api";
import { RATE_PRICED_LABEL } from "@/lib/api/types.generated";
import type { CellAddress } from "@/lib/address";
import { fmtUnit, PLACEHOLDER } from "@/lib/format";
import { fitnessStyle } from "@/lib/derivations";
import { useRead } from "@/lib/hooks/useRead";
import { cx } from "@/lib/cx";
import { CopyButton, ReadState, SegmentedControl, SidePanel } from "@/components/ui";

type SpanTab = "input" | "output" | "details" | "tokens";

const SPAN_TABS = [
  { value: "input", label: "Input" },
  { value: "output", label: "Output" },
  { value: "details", label: "Details" },
  { value: "tokens", label: "Tokens" },
] as const;

function show(v: unknown): string {
  if (v == null) return PLACEHOLDER;
  return typeof v === "string" ? v : JSON.stringify(v, null, 2);
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
        <dd>{span.model ?? PLACEHOLDER}</dd>
        <dt>Provider</dt>
        <dd>{span.provider ?? PLACEHOLDER}</dd>
        <dt>Seconds</dt>
        <dd>{fmtUnit("seconds", span.seconds)}</dd>
        <dt>Config</dt>
        <dd>
          <pre className="cell-pre">{show(span.config)}</pre>
        </dd>
      </dl>
    );
  return (
    <dl className="cell-facts">
      <dt>Input tokens</dt>
      <dd>{span.input_tokens ?? PLACEHOLDER}</dd>
      <dt>Output tokens</dt>
      <dd>{span.output_tokens ?? PLACEHOLDER}</dd>
      <dt>Provider cache</dt>
      <dd>{span.cache_read_tokens ?? PLACEHOLDER}</dd>
      <dt>Billed</dt>
      <dd>{fmtUnit("usd", span.cost_usd)}</dd>
      {span.rate_priced_usd != null && (
        <>
          <dt>{RATE_PRICED_LABEL}</dt>
          <dd>{fmtUnit("usd", span.rate_priced_usd)}</dd>
        </>
      )}
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
            {fmtUnit("rate", cell.fitness)}
          </span>
        )}
        <span className="cell-dim">{fmtUnit("seconds", cell.seconds)}</span>
        <span className="cell-dim">{cell.role}</span>
        <CopyButton data={cell} title="Copy this cell as JSON" />
      </div>
      <div className="cell-answer">
        <div>
          <div className="cell-label">Predicted</div>
          <pre className="cell-pre">{cell.predicted || PLACEHOLDER}</pre>
        </div>
        <div>
          <div className="cell-label">Ground truth</div>
          <pre className="cell-pre">{cell.ground_truth_text}</pre>
        </div>
      </div>
      {cell.error && <p className="note-warn">{cell.error}</p>}
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
                    {sp.model ?? "no model"} · {fmtUnit("seconds", sp.seconds)}
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
  const read = useRead(cellRead(datasetName, cell.answer));
  return (
    <SidePanel
      panelId="cell"
      title={read.status === "ready" ? `Sample ${read.data.position}` : "Sample"}
      onClose={onClose}
      hasPrev={hasPrev}
      hasNext={hasNext}
      onStep={onStep}
    >
      <ReadState read={read} subject="this sample">
        {/* Keyed by the address, so a J/K step starts on the first span again. */}
        {(data) => <CellBody key={cell.answer} cell={data} />}
      </ReadState>
    </SidePanel>
  );
}
