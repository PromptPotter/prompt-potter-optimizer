"use client";
// The LEADER against the ORIGIN, as one box: what it costs to run (prompt length, time per cell),
// then spend, lift, the diff and the rows that changed hands. The Dashboard mounts it at size and
// the chat's run card mounts the same component, so the two cannot disagree.
import { useMemo } from "react";
import { useCycleStream } from "@/lib/poll";
import { useCompareWithOrigin } from "@/lib/hooks/useCompareWithOrigin";
import { useConnector } from "@/lib/hooks/useConnector";
import { useEvidence } from "@/lib/hooks/useEvidence";
import { useObserveSearchPoint } from "@/lib/hooks/useObserveSearchPoint";
import { useRoundRows, type RoundRows } from "@/lib/hooks/useRoundRows";
import {
  METER_WORD,
  candidateObserveConfig,
  observeOptions,
  originAt,
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
import { CELL_MEAN_ROWS } from "@/lib/cell-means";
import { PROMPT_STRING_FIELDS } from "@/lib/prompt-fields";
import { fmtPct0, fmtSigned, fmtUsd } from "@/lib/format";
import { cx } from "@/lib/cx";
import { useViewedLineage } from "@/lib/lineage";
import { useWorkspace } from "@/lib/workspace";
import { Button, CopyButton, HoverCard, SegmentedControl, Term } from "@/components/ui";
import { NodeSurface } from "@/components/shell/node-surface/NodeSurface";
import { SpendBuckets } from "@/components/shell/SpendBuckets";

// Re-read while the run moves; a closed round re-reads at once.
const LIVE_SIGNALS_MS = 30000;

export function LeaderSummary() {
  const { dash } = useCycleStream();
  const cv = useConnector();
  // No node selected: the WHOLE-pipeline view.
  const observe = useObserveSearchPoint(null);
  const summary = runSummary(dash);

  // ROUND 0, read ONCE for the box: the origin diff and the flipped rows both ask about it.
  const origin = useRoundRows(dash ? 0 : null);
  // Which row of it is the origin is the tree's to say; the round document is joined on its label.
  const { viewedPath } = useWorkspace();
  const { index } = useViewedLineage();
  const originLabel = originAt(index, viewedPath)?.course_label ?? null;
  const originCfg =
    originLabel === null
      ? null
      : candidateObserveConfig(origin.doc, originLabel, `origin · ${originLabel}`, null);

  if (!summary) return null;
  return (
    <ConfigBox
      observe={observe}
      summary={summary}
      originCfg={originCfg}
      originPending={origin.loading || originLabel === null}
      origin={origin}
      schema={cv.nodeConfigSchema}
      schemaStatus={cv.pipelineStatus}
      outputSchema={cv.nodeOutputSchema}
    />
  );
}

// What the leader costs to RUN, read first: a shorter prompt and a faster cell are the signals
// that an optimized prompt is better and not merely longer. Each side is a served mean over that
// point's own cells (`SubjectReading.cell_means`); the arrow is the only thing drawn between them,
// never a difference taken here.
function LeadSignals() {
  const { viewedPath } = useWorkspace();
  const { dash, isLive } = useCycleStream();
  const pair = useCompareWithOrigin(viewedPath, null);
  const { evidence } = useEvidence(
    pair.subjects,
    false,
    false,
    false,
    "",
    "",
    isLive ? { intervalMs: LIVE_SIGNALS_MS, revalidateOn: dash?.rounds?.length ?? 0 } : null,
  );

  const [originKey, leaderKey] = pair.subjects;
  const originMeans = evidence?.subjects.find((s) => s.key === originKey)?.cell_means;
  // One subject on the board means the head IS the origin: nothing to stand it against yet.
  const leaderMeans =
    leaderKey === undefined
      ? undefined
      : evidence?.subjects.find((s) => s.key === leaderKey)?.cell_means;

  const rows = CELL_MEAN_ROWS.flatMap((r) => {
    const was = originMeans?.[r.key];
    if (was === undefined) return [];
    return [{ ...r, was, now: leaderMeans?.[r.key] }];
  });
  if (rows.length === 0) return null;

  return (
    <div className="leader-signals">
      <dl>
      {rows.map((r) => (
        <div key={r.key} className={cx("leader-signal", r.lead && "is-lead")}>
          <dt>{r.label}</dt>
          <dd>
            {r.now === undefined ? (
              r.fmt(r.was)
            ) : (
              <>
                <span className="leader-signal-was">{r.fmt(r.was)}</span>
                <span className="leader-signal-arrow" aria-label="origin to leader">
                  →
                </span>
                <span
                  className={cx(
                    "leader-signal-now",
                    r.now < r.was && "is-lower",
                    r.now > r.was && "is-higher",
                  )}
                >
                  {r.fmt(r.now)}
                </span>
              </>
            )}
          </dd>
        </div>
      ))}
      </dl>
      {pair.run && (
        <Button variant="ghost" onClick={pair.run}>
          Open in Compare
        </Button>
      )}
    </div>
  );
}

// The shown searchpoint's rate against its parent's on the same rows. The bench lift only on `best`,
// the pick the bench graded; `23/28` marks a cut-short panel.
function Lift({
  accuracy,
  parentAccuracy,
  benchLift,
  scored,
  expected,
}: {
  accuracy: number | null;
  parentAccuracy: number | null;
  benchLift: number | null;
  scored: number | null;
  expected: number | null;
}) {
  if (accuracy == null) {
    return <span className="run-headline-lift">bench lift {fmtSigned(benchLift)}</span>;
  }
  const cut = scored != null && expected != null && scored < expected;
  return (
    <HoverCard
      className="run-lift-card"
      content={
        <>
          {benchLift != null ? (
            <>
              <p className="run-lift-bench">bench lift {fmtSigned(benchLift)}</p>
              <p className="run-lift-note">
                The pick over the origin in composite fitness, on held-out rows no optimizer node
                read — the same reading for every optimizer.
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

// Spend, lift and changes as one box. All SERVED: the bench lift is `bench_score.lift`, never
// `best − origin`; the floor is `reference_accuracy`.
function ConfigBox({
  observe,
  summary,
  originCfg,
  originPending,
  origin,
  schema,
  schemaStatus,
  outputSchema,
}: {
  observe: ReturnType<typeof useObserveSearchPoint>;
  summary: RunSummary;
  originCfg: ReturnType<typeof candidateObserveConfig>;
  // The round document or the tree naming its origin row has not landed: no diff can be claimed.
  originPending: boolean;
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
      <LeadSignals />
      <div className="run-box-head">
        <div className="run-headline">
          {summary.metered ? (
            <>
              <Term content={<SpendBuckets metered={summary.metered} />}>
                <strong>{fmtUsd(summary.metered.billed_usd)}</strong>
              </Term>
              <span className="run-headline-unit">{METER_WORD.bill}</span>
            </>
          ) : (
            <strong>—</strong>
          )}
          <span className="run-headline-sep" aria-hidden="true">
            ·
          </span>
          <Lift
            accuracy={shownRow?.accuracy ?? null}
            parentAccuracy={shownRow?.referenceAccuracy ?? null}
            benchLift={observe.state === "best" ? summary.benchLift : null}
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
      <RoundFacts last={summary.lastRound} />
      {!cfg ? (
        <p className="run-box-note">
          {observe.loading ? "Loading the searchpoint…" : "Nothing measured yet."}
        </p>
      ) : (
        <details className="run-diff">
          <summary>
            <span className="run-diff-label">{cfg.label}</span>
            {originPending ? (
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

// The optimizer's own facts about its last round, as its runtime worded them; a note is prose, so it hovers.
function RoundFacts({ last }: { last: RunSummary["lastRound"] }) {
  if (!last || last.facts.length === 0) return null;
  return (
    <div className="run-facts" aria-label={`Round ${last.round}, in the optimizer's words`}>
      <span className="run-flip-ref">round {last.round}</span>
      {last.facts.map((f) =>
        f.kind === "stat" ? (
          <span key={f.key}>
            <span className="run-fact-label">{f.label}</span> {f.text}
          </span>
        ) : (
          <HoverCard
            key={f.key}
            className="run-fact-card"
            content={<p className="run-fact-note">{f.text}</p>}
          >
            <span className="run-fact-label" tabIndex={0}>
              {f.label}
            </span>
          </HoverCard>
        ),
      )}
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

// Why best did not move — only on `best`, only when the last round elected nobody. Both the verdict
// (`RoundSummary.improved`) and its sentence (`verdict_reason`) are served, never composed here.
function ChallengerVerdict({
  summary,
  state,
}: {
  summary: RunSummary;
  state: ObserveState;
}) {
  const last = summary.lastRound;
  if (state !== "best" || !last || last.improved !== false || !last.verdictReason) return null;
  return (
    <span className="run-diff-verdict" title={last.verdictReason}>
      round {last.round}: {last.verdictReason}
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
