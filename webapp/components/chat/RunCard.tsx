"use client";
import { useMemo } from "react";
import { useDashboard } from "@/lib/hooks/useDashboard";
import { useConnector } from "@/lib/hooks/useConnector";
import { useObserveSearchPoint } from "@/lib/hooks/useObserveSearchPoint";
import { useRoundRows, type RoundRows } from "@/lib/hooks/useRoundRows";
import {
  candidateObserveConfig,
  isSelfOptimization,
  observeOptions,
  runSummary,
  sampleFlips,
  searchPointDiff,
  searchpointCopyChoices,
  type DiffGroup,
  type ObserveState,
  type ObserveTarget,
  type RunSummary,
  type SampleFlip,
} from "@/lib/derivations";
import type { ElectedRow } from "@/lib/types";
import { PROMPT_STRING_FIELDS } from "@/lib/prompt-fields";
import { runPhaseLabel, stopReasonNextStep } from "@/lib/run-phase";
import { fmtPct0, fmtTheta, fmtUsd } from "@/lib/format";
import { cx } from "@/lib/cx";
import { CopyButton, HoverCard, SegmentedControl, pressable } from "@/components/ui";
import { NodeSurface } from "@/components/shell/node-surface/NodeSurface";
import { HardSamplesPreview } from "@/components/dashboard/samples/HardSamplesPreview";
import { TrendChart } from "@/components/eval/TrendChart";

// The run card inside the chat thread: a miniature Trend (click opens the Dashboard) over boxes that
// each lead with a summary and keep the full thing one disclosure away. `RunSummaryItem` survives a `resume`.

interface Props {
  // From the chat's ONE EventSource — a second `useCycleEvents` would open a second stream.
  sampleOrder: number[] | null;
  onOpenDashboard: () => void;
}

export function RunCard({ sampleOrder, onOpenDashboard }: Props) {
  const { dash, isLive } = useDashboard();
  const cv = useConnector();
  // No node selected: the WHOLE-pipeline view.
  const observe = useObserveSearchPoint(null);
  const summary = runSummary(dash);

  // ROUND 0, read ONCE for the card: the origin diff and the flipped rows both ask about it.
  const origin = useRoundRows(dash ? 0 : null);
  const originCfg = candidateObserveConfig(origin.doc, "C0", "origin · C0", null);

  if (!summary || (summary.rounds === 0 && !isLive)) return null;

  return (
    <section className={cx("run-card", isLive && "is-live")} aria-label="This run" role="region">
      {/* A div, not a button: the `CardFrame` inside is flow content; `pressable` restores activation. */}
      <div
        className="run-box run-trend"
        {...pressable(onOpenDashboard)}
        aria-label="Open the dashboard"
      >
        <TrendChart compact />
      </div>
      <ConfigBox
        observe={observe}
        summary={summary}
        originCfg={originCfg}
        origin={origin}
        schema={cv.nodeConfigSchema}
        schemaStatus={cv.pipelineStatus}
        outputSchema={cv.nodeOutputSchema}
      />
      {/* A pp-self outer cycle has no per-sample roster; the sidebar and L4 rows already `drillInto`. */}
      {!isSelfOptimization(cv.backendType) && (
        <div className="run-box">
          <HardSamplesPreview sampleOrder={sampleOrder} />
        </div>
      )}
    </section>
  );
}

// The shown searchpoint's rate against the origin's on the same rows. θ only on `best` (`ability_delta` is
// the PARENT's lift, per cycle) and never the default (`headline-stats.ts`); `23/28` marks a cut-short panel.
function Lift({
  accuracy,
  parentAccuracy,
  theta,
  scored,
  expected,
}: {
  accuracy: number | null;
  parentAccuracy: number | null;
  theta: number | null;
  scored: number | null;
  expected: number | null;
}) {
  if (accuracy == null) {
    return <span className="run-headline-lift">lift {fmtTheta(theta)}</span>;
  }
  const cut = scored != null && expected != null && scored < expected;
  return (
    <HoverCard
      className="run-lift-card"
      content={
        <>
          {theta != null ? (
            <>
              <p className="run-lift-theta">ability lift {fmtTheta(theta)}</p>
              <p className="run-lift-note">
                The metric the winner is elected on — a logit on this cycle&rsquo;s
                difficulty ruler, not a percentage.
              </p>
            </>
          ) : null}
          <p className="run-lift-note">
            This candidate on the rows it measured, and the parent it was mutated from on
            those same rows.
          </p>
          {cut ? (
            <p className="run-lift-note">
              Measurement stopped at {scored} of the round&rsquo;s {expected} samples — a
              partial panel, not a verdict.
            </p>
          ) : null}
        </>
      }
    >
      <span className="run-headline-acc" tabIndex={0}>
        {fmtPct0(accuracy)}
        {parentAccuracy != null ? (
          <span className="run-headline-was"> from {fmtPct0(parentAccuracy)}</span>
        ) : null}
        {cut ? (
          <span className="run-headline-cut">
            {" "}
            {scored}/{expected}
          </span>
        ) : null}
      </span>
    </HoverCard>
  );
}

// Spend, lift and changes as one box. All SERVED: the lift is `ability_delta` in logits, never
// `best − origin` (`run-summary.ts`); the floor is `matched_parent_accuracy`.
function ConfigBox({
  observe,
  summary,
  originCfg,
  origin,
  schema,
  schemaStatus,
  outputSchema,
}: {
  observe: ReturnType<typeof useObserveSearchPoint>;
  summary: RunSummary;
  originCfg: ReturnType<typeof candidateObserveConfig>;
  origin: RoundRows;
  schema: Parameters<typeof NodeSurface>[0]["schema"];
  schemaStatus: Parameters<typeof NodeSurface>[0]["schemaStatus"];
  outputSchema: Parameters<typeof NodeSurface>[0]["outputSchema"];
}) {
  const options = observeOptions(observe.avail);
  const cfg = observe.cfg;
  const diff = useMemo(() => searchPointDiff(originCfg, cfg), [originCfg, cfg]);
  // ONE subject for the whole box: rate, diff and flipped rows all describe the picked searchpoint.
  const target = observe.target;
  const shownRound = useRoundRows(target && target.round > 0 ? target.round : null);
  const shown = target?.round === 0 ? origin : shownRound;
  const shownRow = target ? shown.row(target.idx) : null;

  return (
    <div className="run-box">
      <div className="run-box-head">
        <div className="run-headline">
          <strong>{summary.usedUsd != null ? fmtUsd(summary.usedUsd) : "—"}</strong>
          <span className="run-headline-unit">spent</span>
          <span className="run-headline-sep" aria-hidden="true">
            ·
          </span>
          <Lift
            accuracy={shownRow?.accuracy ?? null}
            parentAccuracy={shownRow?.matchedParentAccuracy ?? null}
            theta={observe.state === "best" ? summary.abilityDelta : null}
            scored={shownRow?.n_samples ?? null}
            expected={shownRow?.n_expected ?? null}
          />
        </div>
        {options.length > 1 ? (
          <SegmentedControl<ObserveState>
            options={options}
            value={observe.state}
            onChange={observe.setPref}
            ariaLabel="Which searchpoint to show"
          />
        ) : null}
        {/* Same builder as the searchpoint drill-in; the text form is a prompt a human reads. */}
        <CopyButton
          choices={[
            ...searchpointCopyChoices({ cfg, row: shownRow }),
            ...(cfg
              ? [{ key: "text", label: "Prompt + config as text", data: copyPayload(cfg) }]
              : []),
          ]}
          title="Copy this searchpoint"
        />
      </div>
      {!cfg ? (
        <p className="run-box-note">
          {observe.loading ? "Loading the searchpoint…" : "Nothing measured yet."}
        </p>
      ) : (
        <details className="run-diff">
          <summary>
            <span className="run-diff-label">{cfg.label}</span>
            {origin.loading ? (
              <span className="run-diff-changes">comparing to origin…</span>
            ) : diff.length === 0 ? (
              <span className="run-diff-changes">identical to the origin you submitted</span>
            ) : (
              <span className="run-diff-changes">
                {diff.map((g) => (
                  <DiffChip key={`${g.kind}:${g.node ?? ""}`} group={g} />
                ))}
              </span>
            )}
            <ChallengerVerdict summary={summary} state={observe.state} />
          </summary>
          <NodeSurface
            node={null}
            point={{ origin_prompt_fields: cfg.promptFields, pipeline_overlay: {} }}
            overlay={cfg.config}
            schema={schema}
            schemaStatus={schemaStatus}
            outputSchema={outputSchema}
            mode="values"
            compact
          />
        </details>
      )}
      <Flips origin={origin} shown={shown} shownRow={shownRow} target={target} />
    </div>
  );
}

// The glyph is the KEY — ✎ prompt, ⚙ node config — decoration over the `aria-label`, never its only carrier.
function DiffChip({ group }: { group: DiffGroup }) {
  const what = group.kind === "prompt" ? "prompt" : (group.node ?? "pipeline");
  return (
    <span className="run-diff-group" aria-label={`${what} changed: ${group.names.join(", ")}`}>
      <span className="run-diff-key" aria-hidden="true">
        {group.kind === "prompt" ? "✎" : "⚙"}
      </span>
      {group.node ? (
        <span className="run-diff-node" aria-hidden="true">
          {group.node}
        </span>
      ) : null}
      <span aria-hidden="true">{group.names.join(", ")}</span>
    </span>
  );
}

// Why best is still the origin — only on `best`, only when the last round elected nobody. The verdict is
// the served `RoundSummary.improved`: a challenger's subset rate is not comparable to the origin's.
function ChallengerVerdict({
  summary,
  state,
}: {
  summary: RunSummary;
  state: ObserveState;
}) {
  const last = summary.lastRound;
  if (state !== "best" || !last || last.improved !== false || last.candidates === 0) return null;
  return (
    <span className="run-diff-verdict" title={last.verdictReason ?? undefined}>
      round {last.round}: {last.candidates} candidate{last.candidates === 1 ? "" : "s"} ran, none
      beat the origin
    </span>
  );
}

// Prompt fields in canonical order plus resolved config, verbatim. NOT the runnable string: that is
// backend `PromptTemplate.compile_prompt()`, which nothing serves — never re-implement it here.
function copyPayload(cfg: {
  promptFields: Record<string, unknown>;
  config: Record<string, unknown>;
}): string {
  const lines: string[] = [];
  for (const key of PROMPT_STRING_FIELDS) {
    const v = cfg.promptFields[key];
    if (typeof v === "string" && v.trim()) lines.push(`${key}:\n${v.trim()}\n`);
  }
  if (lines.length === 0) lines.push("(this searchpoint carries no prompt fields)\n");
  lines.push(`pipeline config:\n${JSON.stringify(cfg.config, null, 2)}`);
  return lines.join("\n");
}

// Rows this searchpoint turned around vs the origin, as a partition that CLOSES (both measured, the two
// directions, the remainder). Follows the picker; silent on the origin, where `ChallengerVerdict` speaks.
function Flips({
  origin,
  shown,
  shownRow,
  target,
}: {
  origin: RoundRows;
  shown: RoundRows;
  shownRow: ElectedRow | null;
  target: ObserveTarget | null;
}) {
  const originRow = origin.row(0);
  const comparable = !!target && target.round > 0 && !!originRow && !!shownRow;

  const flips = useMemo(() => {
    if (!comparable) return null;
    return sampleFlips(origin.samples(originRow), shown.samples(shownRow));
  }, [comparable, origin, originRow, shown, shownRow]);

  if (!flips || flips.compared === 0) return null;
  const { gained, lost, compared, unchanged } = flips;
  return (
    <div className="run-flips">
      {/* NAMED: these rows are vs the campaign ORIGIN, not the parent floor of the percent pair above. */}
      <span className="run-flip-ref">vs origin</span>
      <FlipSep />
      <span className="run-flip-total">{compared} both measured</span>
      {gained.length > 0 ? <FlipIds label="now right" kind="gained" flips={gained} /> : null}
      {lost.length > 0 ? <FlipIds label="now wrong" kind="lost" flips={lost} /> : null}
      <FlipSep />
      <span className="run-flip-same">
        {gained.length + lost.length === 0
          ? "no row changed hands — the lift is elsewhere"
          : `${unchanged} unchanged`}
      </span>
    </div>
  );
}

// Carried by the segment that FOLLOWS it, so a segment rendering nothing takes its separator along.
function FlipSep() {
  return (
    <span className="run-flip-sep" aria-hidden="true">
      ·
    </span>
  );
}

function FlipIds({
  label,
  kind,
  flips,
}: {
  label: string;
  kind: "gained" | "lost";
  flips: SampleFlip[];
}) {
  return (
    <>
      <FlipSep />
      <HoverCard
        className="run-flip-card"
        content={
          <ul className="run-flip-list">
            {flips.map((f) => (
              <li key={f.sample_id}>
                <span className="run-flip-id">#{String(f.sample_id).padStart(3, "0")}</span>
                <span className="run-flip-was">{f.before.predicted || "∅"}</span>
                <span aria-hidden="true">→</span>
                <span className="run-flip-now">{f.after.predicted || "∅"}</span>
                <span className="run-flip-gt">want {f.after.ground_truth || "—"}</span>
              </li>
            ))}
          </ul>
        }
      >
        <span className={cx("run-flip-line", `is-${kind}`)} tabIndex={0}>
          <strong>{flips.length}</strong> {label}
        </span>
      </HoverCard>
    </>
  );
}

// The FROZEN thread item: captured VALUES, never a live read — a resume or `resume --from N` rewrites
// the files a live pane would re-read.
export function RunSummaryItem({ summary }: { summary: RunSummary }) {
  return (
    <div className="chat-msg ai run-summary-item" role="note">
      <span className="run-summary-title">
        Run finished
        {summary.stopReason ? ` · ${runPhaseLabel("terminal", summary.stopReason)}` : ""}
      </span>
      {stopReasonNextStep(summary.stopReason) ? (
        <p className="run-summary-next">{stopReasonNextStep(summary.stopReason)}</p>
      ) : null}
      {/* θ does not appear: a log line has no hover to hide jargon behind. */}
      <span className="run-summary-line">
        {summary.championLabel ? `${summary.championLabel} · ` : ""}
        {summary.accuracy != null ? fmtPct0(summary.accuracy) : "—"}
        {summary.accuracy != null && summary.parentAccuracy != null
          ? ` from ${fmtPct0(summary.parentAccuracy)}`
          : ""}
        {` · ${summary.rounds} rounds`}
        {summary.usedUsd != null ? ` · ${fmtUsd(summary.usedUsd)}` : ""}
      </span>
      {summary.changes ? <p className="run-summary-changes">{summary.changes}</p> : null}
    </div>
  );
}
