import { useState } from "react";
import { cellRead } from "@/lib/api";
import {
  type Cell,
  type CellRow,
  type CellSpan,
  RATE_PRICED_LABEL,
  SAMPLE_STATUS_LABELS,
} from "@/lib/api/types";
import { fmtUnit, PLACEHOLDER } from "@/lib/format";
import { fitnessStyle } from "@/lib/derivations";
import { useRead } from "@/lib/hooks/useRead";
import { cx } from "@/lib/cx";
import { CopyButton, ReadState, SegmentedControl, SidePanel } from "@/components/ui";

export function Fitness({ value }: { value: number | null | undefined }) {
  if (value == null) return <span className="ms-dim">—</span>;
  return (
    <span className="ms-fit" style={fitnessStyle(value)}>
      {fmtUnit("rate", value)}
    </span>
  );
}

// A row and the opened cell say a cell's verdict one way: its word, then the grade it is the verdict of.
export function Verdict({ cell }: { cell: Pick<CellRow, "status" | "fitness"> }) {
  return (
    <span className="ms-verdict">
      <span className={cx("ms-status", `is-${cell.status.toLowerCase()}`)}>
        {SAMPLE_STATUS_LABELS[cell.status]}
      </span>
      {cell.fitness != null && <Fitness value={cell.fitness} />}
    </span>
  );
}

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
        <Verdict cell={cell} />
        <span className="ms-dim">{fmtUnit("seconds", cell.seconds)}</span>
        {cell.cached && <span className="cell-tag">replayed</span>}
        <span className="ms-dim">{cell.role}</span>
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
      {cell.error && (
        <div>
          <div className="cell-label">Error</div>
          <pre className="cell-pre">{cell.error}</pre>
        </div>
      )}
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
                  <span className="ms-dim">
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
  answer,
  onClose,
  hasPrev,
  hasNext,
  onStep,
}: {
  datasetName: string;
  answer: string;
  onClose: () => void;
  hasPrev: boolean;
  hasNext: boolean;
  onStep: (shift: -1 | 1) => void;
}) {
  const read = useRead(cellRead(datasetName, answer));
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
        {(data) => <CellBody key={answer} cell={data} />}
      </ReadState>
    </SidePanel>
  );
}
