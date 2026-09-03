"use client";
import { useMemo } from "react";
import type { DatasetItem, HardSampleOrder, HardSamplesScope, SampleSeries } from "@/lib/api";
import type { SeriesTotals } from "@/lib/hooks/useDatasetPreview";
import { useDashboard } from "@/lib/hooks/useDashboard";
import { useConnector } from "@/lib/hooks/useConnector";
import { useObserveSearchPoint } from "@/lib/hooks/useObserveSearchPoint";
import { useRoundCandidates } from "@/lib/hooks/useRoundCandidates";
import { useRoundFile } from "@/lib/hooks/useRoundFile";
import { useWorkspace } from "@/lib/workspace";
import {
  candidateObserveConfig,
  isSelfOptimization,
  observeOptions,
  runSummary,
  sampleFlips,
  samplesForRow,
  searchPointDiff,
  type DiffGroup,
  type ObserveState,
  type ObserveTarget,
  type RunSummary,
  type SampleFlip,
} from "@/lib/derivations";
import type { RoundResult } from "@/lib/types";
import { PROMPT_STRING_FIELDS } from "@/lib/prompt-fields";
import { fmtPct0, fmtUsd } from "@/lib/format";
import { cx } from "@/lib/cx";
import { CopyButton, HoverCard, SegmentedControl } from "@/components/ui";
import { NodeSurface } from "@/components/shell/node-surface/NodeSurface";
import { HardSamplesPreview } from "@/components/dashboard/samples/HardSamplesPreview";

// The run card — what the operator came for, inside the thread rather than in the
// chrome above it. TWO boxes on the chat's own background, not one shaded compartment,
// and neither wears a title: a box that has to announce what it is has already failed
// to show it, and three title rows cost three of the six lines this card gets.
//
// The split is by QUESTION, and there are only two. What did the optimizer do and what
// did it buy — spend, lift, the fields it changed, the rows that changed hands. And
// where is the run in the data — the walk, and the shape of the roster it walks.
//
// Each box leads with a SUMMARY and keeps the full thing one disclosure away. That is
// the whole design: it says "Task intent, llm_only.model" rather than printing two
// prompts, and three sample lines rather than a table. Compact by default, complete
// on click.
//
// LIVE the card follows the 2 s poll; stopped it sits in the log. The separate frozen
// item (`RunSummaryItem`) is what survives a `resume` — see the note there.

interface Props {
  // The declared scoring order, from the chat's ONE EventSource. Threaded rather than
  // subscribed here — a second `useCycleEvents` would open a second stream.
  sampleOrder: number[] | null;
}

export function RunCard({ sampleOrder }: Props) {
  const { dash, isLive } = useDashboard();
  const { viewedPath } = useWorkspace();
  const cv = useConnector();
  // No node is selected here, so this is the WHOLE-pipeline view: the prompt plus
  // config across every node — the shape a reader means by "my prompt".
  const observe = useObserveSearchPoint(null);
  const summary = runSummary(dash);

  // ROUND 0, fetched ONCE for the whole card. Both the "changed vs origin" summary
  // and the flipped rows are questions about the origin, and two hooks asking for the
  // same file would be two GETs of one static document.
  const originFile = useRoundFile(viewedPath, dash ? 0 : null);
  const originCfg = candidateObserveConfig(originFile.doc, "C0", "origin · C0", null);

  // Nothing measured, nothing to show. Silence beats an empty frame — the ingest
  // thread above is the surface at that point.
  if (!summary || (summary.rounds === 0 && !isLive)) return null;

  return (
    <section className={cx("run-card", isLive && "is-live")} aria-label="This run" role="region">
      <ConfigBox
        observe={observe}
        summary={summary}
        originCfg={originCfg}
        originDoc={originFile.doc}
        originLoading={originFile.loading}
        schema={cv.nodeConfigSchema}
        outputSchema={cv.nodeOutputSchema}
      />
      {/* A pp-self outer cycle has no per-sample roster — its "samples" are inner
          campaigns. It renders no box at all rather than a pointer at the inner
          run: the sidebar row and the L4 panel rows already fire that same
          `drillInto`, and the hero above already says the backend is PromptPotter. */}
      {!isSelfOptimization(cv.backendType) && (
        <div className="run-box">
          <HardSamplesPreview sampleOrder={sampleOrder} />
        </div>
      )}
    </section>
  );
}

function fmtTheta(v: number | null): string {
  return v != null ? `θ ${v >= 0 ? "+" : ""}${v.toFixed(2)}` : "—";
}

// What the shown searchpoint bought, in the units a reader already owns: its own rate
// and the origin's on the same rows.
//
// θ is the number the engine ELECTS on and it is the honest one — but it is a logit on
// a difficulty ruler, and `headline-stats.ts` states the rule this follows: θ is
// jargon, never the forced default. So it moves into the card behind the figure, where
// it explains the percent instead of competing with it.
//
// `theta` arrives only when the picker is on `best`, because `ability_delta` is the
// PARENT's lift over origin — served per cycle, not per candidate. Showing it beside
// another candidate's rate would caption one individual with another's number.
//
// The pair is subset-matched, which is why it may be read as a before/after at all. The
// flips line below deliberately names a DIFFERENT floor, and says so.
// An absent floor prints nothing rather than a zero, and a searchpoint with no measured
// rate falls back to θ so the lift is never simply missing.
//
// `23/28` trails the pair when the panel was CUT SHORT — PoBB stopping a losing arm, or
// an escalation abort. It is the rate's own basis, so it rides against the rate in the
// card's quietest weight: a stopped arm is routine, and a badge would shout on most
// rounds. Not a verdict (`_candidate_fate` states why) and not a progress bar —
// `expected_samples` lands at round close, so an in-flight candidate shows nothing.
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

// Box 1 — what the run cost, what it bought, and what it changed to buy it. One box
// because those are one question: a spend with no lift beside it is a bill, and a
// lift with no spend beside it is a boast.
//
// The headline numbers are all SERVED. The lift is `ability_delta` in logits, never a
// percent and never `best − origin` (`run-summary.ts` states why); the accuracy pair
// is the champion's own rate against `matched_parent_accuracy`, the origin restricted
// to the rows that candidate actually measured — the only honest floor under
// elimination. Nothing here subtracts, defaults or rounds a number into existence.
function ConfigBox({
  observe,
  summary,
  originCfg,
  originDoc,
  originLoading,
  schema,
  outputSchema,
}: {
  observe: ReturnType<typeof useObserveSearchPoint>;
  summary: RunSummary;
  originCfg: ReturnType<typeof candidateObserveConfig>;
  originDoc: RoundResult | null;
  originLoading: boolean;
  schema: Parameters<typeof NodeSurface>[0]["schema"];
  outputSchema: Parameters<typeof NodeSurface>[0]["outputSchema"];
}) {
  const options = observeOptions(observe.avail);
  const cfg = observe.cfg;
  const diff = useMemo(() => searchPointDiff(originCfg, cfg), [originCfg, cfg]);
  // ONE subject for the whole box: whichever searchpoint the picker names is what the
  // rate, the diff and the flipped rows all describe. Splitting it — a run-level rate
  // over a candidate-level diff — is what made the picker read as half-broken.
  const { byRound } = useRoundCandidates();
  const shownRow = observe.target
    ? (byRound.get(observe.target.round)?.[observe.target.idx] ?? null)
    : null;

  return (
    <div className="run-box">
      <div className="run-box-head">
        {/* A div, not a <p>: `Lift` hangs a HoverCard off the accuracy pair, and that
            renders a <div> — which a <p> may not contain. Invalid nesting there is a
            HYDRATION error, not a lint nit: the server and client trees disagree. */}
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
        {cfg ? <CopyButton data={copyPayload(cfg)} title="Copy this prompt and config" /> : null}
      </div>
      {!cfg ? (
        <p className="run-box-note">
          {observe.loading ? "Loading the searchpoint…" : "Nothing measured yet."}
        </p>
      ) : (
        <details className="run-diff">
          <summary>
            <span className="run-diff-label">{cfg.label}</span>
            {originLoading ? (
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
            configSeed={cfg.config}
            schema={schema}
            outputSchema={outputSchema}
            mode="values"
            compact
          />
        </details>
      )}
      <Flips originDoc={originDoc} target={observe.target} />
    </div>
  );
}

// One group of the change summary. The glyph is the KEY — ✎ the prompt, ⚙ a node's
// config — and the node is written once in front of its params instead of prefixing
// every one of them. `changed:` is gone with it: the glyph already says that.
//
// Plain spans, no icon set: two characters carry the whole vocabulary, and they are
// decoration over the `aria-label`, never the only carrier.
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

// Why the best searchpoint is still the origin. Shown ONLY on `best`, and only when
// the last round elected nobody — the case that otherwise reads as a broken surface:
// "Best · C0" after two challengers ran looks identical to "nothing has been tried",
// and those are opposite facts about a run.
//
// The verdict is the engine's own (`RoundSummary.improved`), never a re-comparison
// here. A challenger's accuracy is NOT comparable to the origin's full-set rate — it
// measured its own subset — which is exactly why this defers to the served flag
// instead of subtracting two numbers that answer different questions.
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

// What the copy button hands over. The prompt FIELDS in canonical order plus the
// resolved config — both verbatim served values.
//
// It is NOT the runnable string the backend sent: that is `PromptTemplate.
// compile_prompt()`, backend logic which nothing serves to this app, and
// re-implementing it here would produce a second compiler that drifts silently.
// A literal runnable prompt has to be SERVED before it can be copied.
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

// Which rows this searchpoint turned around, against the origin — the answer a reader
// trusts when an aggregate leaves them cold, and the last line of the "what did it buy"
// box. The regressions ride along deliberately: a card that shows only the wins is an
// advertisement.
//
// ONE line, and a partition that CLOSES: rows both sides measured, the two directions,
// then the remainder. It ran as two lines each ending "of 28 compared" — a reader adding
// 2 and 2 against 28 read a panel that was lying. The arithmetic was right; the 24 that
// stayed put was simply never printed, and it is the largest group in every real run.
//
// It follows the PICKER. It used to pin itself to the champion whatever the picker
// showed, on the argument that the question is "what did the optimization buy" rather
// than "what is the pane pointed at" — but on one surface that made a control which
// changed the label and the diff and silently not this, which reads as a stuck line
// rather than a deliberate scope. One subject per box: whatever is named above is what
// these rows are about.
//
// SILENT when the shown searchpoint IS the origin. `ChallengerVerdict` already says so
// with the round's own served `improved`, and two channels for one fact is the
// redundancy that made this card long.
function Flips({
  originDoc,
  target,
}: {
  originDoc: RoundResult | null;
  target: ObserveTarget | null;
}) {
  const { dash } = useDashboard();
  const { viewedPath } = useWorkspace();
  const { byRound } = useRoundCandidates();
  const originRow = byRound.get(0)?.[0] ?? null;
  const shownRow = target ? (byRound.get(target.round)?.[target.idx] ?? null) : null;
  // Round 0 = the origin itself; there is nothing to compare it against but itself, so
  // its file is not fetched twice.
  const comparable = !!target && target.round > 0 && !!originRow && !!shownRow;
  const shownFile = useRoundFile(viewedPath, comparable && target ? target.round : null);

  const flips = useMemo(() => {
    if (!comparable || !originRow || !shownRow) return null;
    return sampleFlips(
      samplesForRow(originRow, dash, originDoc),
      samplesForRow(shownRow, dash, shownFile.doc),
    );
  }, [comparable, originRow, shownRow, dash, originDoc, shownFile.doc]);

  if (!comparable || !flips || flips.compared === 0) return null;
  const { gained, lost, compared, unchanged } = flips;
  return (
    <div className="run-flips">
      {/* The reference is NAMED, because it is not the one the percent pair above uses.
          That pair's floor is the candidate's parent; these rows are the campaign
          ORIGIN, the only per-sample panel served to this app — a parent's rows on a
          re-subsetted round are overwritten by the winner's on the round document. Two
          references in one box is a defect; two references with one of them unlabelled
          is the defect that files bug reports. */}
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

// The mid-dot, carried by the segment that FOLLOWS it so a segment rendering nothing
// takes its separator with it. Same grammar as the headline line above.
function FlipSep() {
  return (
    <span className="run-flip-sep" aria-hidden="true">
      ·
    </span>
  );
}

// One direction of the partition — the count — with the ids and their before→after
// answers in the hover card behind it.
//
// The ids used to print inline, on the argument that a truncated list of what a run
// fixed would be the one place this card rounded off. Eleven ids run wider than every
// other line in the box, and the count is what a reader acts on; the ids are what they
// check afterwards. Nothing is truncated — the card holds all of them, with the
// evidence the inline version could only fit on `title`.
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

// The FROZEN thread item: what a finished run leaves behind once a `resume` has moved
// the live card on to the next one.
//
// It renders captured VALUES, never a live read — that is the whole point. A resume
// re-animates `dashboard.json` and `resume --from N` can rewrite the very round files
// a live pane would re-read, so anything that kept a pointer would quietly restate
// itself as the new run. This holds numbers and stops.
export function RunSummaryItem({ summary }: { summary: RunSummary }) {
  return (
    <div className="chat-msg ai run-summary-item" role="note">
      <span className="run-summary-title">
        Run finished{summary.stopReason ? ` · ${summary.stopReason}` : ""}
      </span>
      {/* Same vocabulary as the live card it replaces: the percent pair leads and θ
          does not appear. A log line has no hover to hide jargon behind, so the one
          number a reader can act on is the only one it prints. `abilityDelta` stays
          on the record — this is what the line SHOWS, not what it holds. */}
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
