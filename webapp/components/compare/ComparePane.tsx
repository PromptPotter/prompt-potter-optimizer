"use client";
// The Compare tab — pick any SUBJECTS from any datasets and read what they jointly say.
//
// It exists because the question "I ran this four times, now what?" is about a SET, and the only
// surface that answered it lived inside one campaign's dashboard, gated to the self-optimizing
// loop. There is no L4 gate here and no dataset scope: an ordinary campaign and a pp-self one
// take the same path, and a selection that spans datasets is allowed and then reported on.
//
// A channel is anchored on a campaign (its root origin), a course (one branch, at the winner its
// last election crowned) or a single candidate — three kinds, one arithmetic, because the server
// reduces all three to per-cell values before anything is computed.
//
// Cheap reads auto-load on any selection change — including the metric, every interval and every
// pairwise test, which are arithmetic over values the roster read already had in hand. The edit
// ranking and the per-channel winner chain are the two walks expensive enough to sit behind a press
// (see `useEvidence`).

import { useCallback, useMemo, useState } from "react";
import type { Evidence, SubjectReading } from "@/lib/api";
import { CardFrame, SegmentedControl, Toolbar, ToolbarSep } from "@/components/ui";
import { cx } from "@/lib/cx";
import { useCompareSelection } from "@/lib/compare-selection";
import { useEvidence } from "@/lib/hooks/useEvidence";
import {
  effectTone,
  fmtMetricInterval,
  fmtMetricValue,
  fmtSigned,
  shortId,
} from "@/lib/format";
import { maskedSubject } from "@/lib/api/reads";
import { ChannelCards } from "./ChannelCards";
import { Coverage, EvidenceCharts, SeriesLegend, type CompareView } from "./EvidenceCharts";
import { ChannelMask } from "./ChannelMask";
import { NO_EDITS, type ScenarioEdits } from "./config-edit";
import { isCustomMetric, MetricExpression, MetricPicker } from "./MetricPicker";
import { PairwisePanel } from "./PairwisePanel";
import { SearchpointCards } from "./SearchpointPanels";

const VIEWS: readonly { value: CompareView; label: string; title: string }[] = [
  { value: "grouped", label: "Grouped", title: "One bar per subject, grouped by cell" },
  { value: "overlaid", label: "Stacked", title: "One bar per cell, subjects sharing it" },
  { value: "lines", label: "Lines", title: "One line per subject across the cells" },
  {
    value: "merged",
    label: "Merged",
    title: "Cells merged: one estimate per subject, with its 95% interval",
  },
];

// The SENTENCE is served (`Comparability.note`); a per-reason text map here would be a second
// copy free to drift out of step with the terminal's. Only the tone is a rendering choice, and
// it reads `verdict`, whose `null` is UNKNOWN and never a yes.
function comparabilityTone(verdict: boolean | null): string {
  return verdict === true ? "l4-note" : "l4-warn";
}

// What each channel is called, keyed by the served subject key — built once so the pairwise table
// names a subject exactly as the legend does.
function channelNames(subjects: readonly SubjectReading[]): ReadonlyMap<string, string> {
  return new Map(
    subjects.map((s) => [s.key, s.kind === "campaign" ? shortId(s.label) : s.label]),
  );
}

export function ComparePane() {
  // The selection is shell-level, not pane-level: a searchpoint is added from the surface it is
  // being looked at, which is a different tab (`lib/compare-selection.tsx`).
  const {
    channels,
    subjects: selected,
    hasSubject,
    addSubject,
    replace,
    remove,
  } = useCompareSelection();
  const [view, setView] = useState<CompareView>("grouped");
  // Which channel's scoring mask is open — by its BARE address, not its key: applying a mask changes
  // the key, and holding that would close the form on the edit it just accepted. One at a time,
  // because the editor is a form and two side by side is a second answer to "which am I editing".
  const [masking, setMasking] = useState<string | null>(null);
  const [ranking, setRanking] = useState(false);
  const [winnerChain, setWinnerChain] = useState(false);
  // On by default, unlike the other two: "what are these two things" is the first question asked
  // of a searchpoint comparison, and it costs no extra document — the head's round file is
  // already open. It is a toggle only so a wall of prompt text can be put away.
  const [config, setConfig] = useState(true);
  // Per-channel configuration edits — the OTHER kind of mask, and the one nothing can preview:
  // no measurement exists at an edited value, so it invalidates rather than re-projects
  // (`config-edit.tsx`). Held here because it spans the two cards that render it.
  const [edits, setEdits] = useState<ScenarioEdits>(NO_EDITS);
  // ONE opaque selector — a catalogue key or a composed `expr:…`, both the server's spellings.
  // Empty means "unset": `fetchEvidence` then omits the query param and the SERVER picks its own
  // default, so the browser never needs a second copy of what that default is.
  const [metric, setMetric] = useState("");
  const { evidence, loading, error, invalidMetric } = useEvidence(
    selected,
    ranking,
    winnerChain,
    config,
    metric,
  );

  // Turning the selection over invalidates the ranking press — the walk was for a different
  // set — and the metric with it: the catalogue is per-selection, so one subject's channel may
  // not be answerable by the next lot, and holding a stale pick 400s on the very next read. The
  // edits go too: carried into a new selection they would mark a channel unknown on the strength
  // of a change made to a different one.
  //
  // Keyed on the CAMPAIGNS, and watched rather than wired to a click. Watched, because the
  // selection is written from three places now — a sidebar tick, the dashboard's "compare this
  // searchpoint", this pane's own ✕ — and a reset hung off one of them was never run by the other
  // two. Campaigns rather than subjects, because re-POINTING a channel is not a new selection:
  // the catalogue is per-selection and the campaign set has not moved, so the operator's metric
  // survives a walk through a lineage, which is exactly when they are using it.
  const campaignsKey = useMemo(
    () => [...new Set(channels.map((c) => c.rootCampaignId))].sort().join("~"),
    [channels],
  );
  const [seenCampaigns, setSeenCampaigns] = useState(campaignsKey);
  if (campaignsKey !== seenCampaigns) {
    setSeenCampaigns(campaignsKey);
    setRanking(false);
    setMetric("");
    setEdits(NO_EDITS);
    setMasking(null);
  }

  const repoint = useCallback(
    (from: string, to: string) => {
      setMasking(null);
      replace(from, to);
    },
    [replace],
  );

  const comparability = evidence?.comparability ?? null;
  // Whether the selected metric read ANYTHING. Everything below the picker describes a number
  // that then does not exist, so it stays silent rather than restating the same absence four ways.
  const readable = !!evidence?.subjects.some((c) => c.n_cells > 0);
  const names = useMemo(() => channelNames(evidence?.subjects ?? []), [evidence?.subjects]);
  // The channel the editor is open on, resolved against the LAST GOOD read: a rejected formula
  // keeps the prior evidence (`useFetch` `survive:"invalid"`), and losing the form on a typo is
  // the failure that rule exists to prevent.
  const maskTarget =
    evidence?.subjects.find((s) => maskedSubject(s, {}) === masking) ?? null;
  // A campaign is its origin and nothing precedes it, so the toggle only earns its place once a
  // branch or a searchpoint is on the board.
  const hasBranch = !!evidence?.subjects.some((s) => s.kind !== "campaign");

  return (
    <div className="content" id="content-compare">
        <div className="cmp-main">
          {/* No picker row. Which campaigns are on the board is ticked in the SIDEBAR, on the
              rows that already list them — a toolbar menu here was a second copy of that list
              two inches to its right, and it cost the tab a permanent row. */}
          {selected.length === 0 ? (
            <CardFrame title="Compare" headingTag="h2">
              <p className="l4-lede">
                {/* "the campaign list", not "the sidebar" — the same component is a
                    docked sidebar on a desktop and the whole screen behind ← on a phone,
                    and a phone reader sent to a sidebar that is not there is sent nowhere.
                    Its own header says CAMPAIGNS at both widths. */}
                Tick <strong>▢</strong> beside two or more campaigns in the campaign list. Each lands on
                its own winner; open a channel&rsquo;s lineage to walk its cladogram — click any
                searchpoint to move that channel onto it, or to put it on the board beside the
                winner. They may come from different datasets — the read says what that costs
                rather than refusing it.
              </p>
            </CardFrame>
          ) : error ? (
            <CardFrame title="Compare" headingTag="h2">
              <p className="l4-warn">Could not read the selection: {error}</p>
            </CardFrame>
          ) : !evidence || loading ? (
            <CardFrame title="Compare" headingTag="h2">
              <p className="l4-empty">Reading {selected.length} channel(s)…</p>
            </CardFrame>
          ) : (
            <>
              {/* The channels themselves, side by side: each lands on its campaign's winner and
                  carries the lineage map that moves it. Everything below is what the SET says
                  jointly — this is what each one of them IS. */}
              <ChannelCards
                evidence={evidence}
                channels={channels}
                edits={edits}
                onEdits={setEdits}
                onReplace={repoint}
                onAdd={addSubject}
                hasSubject={hasSubject}
                onRemove={remove}
              />

              {/* WHAT these channels are, before what they scored — the panel the operator
                  reaches a searchpoint comparison for, and the one they open the tab on. Above
                  the statistics because a configuration difference is what every number below is
                  evidence ABOUT; it sat under them for a release, which put the answer before the
                  question. */}
              {config ? (
                <SearchpointCards
                  evidence={evidence}
                  loading={loading}
                  edits={edits}
                  onEdits={setEdits}
                />
              ) : (
                <CardFrame title="How these searchpoints are configured" headingTag="h2">
                  <p className="l4-lede">
                    Not fetched.{" "}
                    <button
                      type="button"
                      className="cmp-link"
                      onClick={() => setConfig(true)}
                    >
                      Show configurations
                    </button>
                  </p>
                </CardFrame>
              )}

              <CardFrame
                title="Levels"
                headingTag="h2"
                actions={
                  // Two control groups, one row — the header's own rule. A bare fragment leaves
                  // the flex to push only the last child right and the pair reads as a pile.
                  <Toolbar>
                    <MetricPicker
                      reading={evidence.metric}
                      metric={metric}
                      onMetric={setMetric}
                    />
                    <ToolbarSep />
                    <SegmentedControl
                      options={VIEWS}
                      value={view}
                      onChange={setView}
                      ariaLabel="Chart form"
                    />
                  </Toolbar>
                }
              >
                {isCustomMetric(metric) && (
                  <MetricExpression
                    reading={evidence.metric}
                    metric={metric}
                    invalid={invalidMetric}
                    onMetric={setMetric}
                  />
                )}
                {/* A rejection the expression input is not on screen to carry. `useEvidence`
                    keeps the last good read on an invalid metric, so without this the pane
                    would go on showing those numbers with nothing said. */}
                {invalidMetric && !isCustomMetric(metric) && (
                  <p className="l4-warn">{invalidMetric}</p>
                )}
                {/* Selected, and not in anything below. A subject that thins the selection in
                    silence is the channel-level twin of scoring an unread cell as zero. */}
                {evidence.unread_subjects.length > 0 && (
                  <p className="l4-note">
                    Selected but not read: {evidence.unread_subjects.join(", ")}. Each has nothing
                    measured at the point it addresses, so it is absent from every number here
                    rather than counted as a low one.
                  </p>
                )}
                {readable ? (
                  <>
                    {comparability && (
                      <p className={comparabilityTone(comparability.verdict)}>
                        {comparability.note}
                      </p>
                    )}
                    <EvidenceCharts evidence={evidence} view={view} />
                    <SeriesLegend
                      evidence={evidence}
                      masking={masking}
                      onMask={(address) =>
                        setMasking((prev) => (prev === address ? null : address))
                      }
                    />
                    {maskTarget && (
                      <ChannelMask
                        // KEYED on which channel is open. The editor holds a local draft, and
                        // switching channels reuses the same position in the tree — unkeyed, the
                        // next channel opens holding the previous one's criterion.
                        key={masking}
                        subject={maskTarget}
                        invalid={invalidMetric}
                        onApply={replace}
                        onClose={() => setMasking(null)}
                      />
                    )}
                    <Coverage evidence={evidence} />
                    <MetricLede evidence={evidence} view={view} names={names} />
                  </>
                ) : (
                  <MetricUnavailable evidence={evidence} names={names} />
                )}
              </CardFrame>

              {readable && <Scenarios evidence={evidence} />}

              {readable && (
                <>
                  <CardFrame
                    title="Branches behind these channels"
                    headingTag="h2"
                    actions={
                      hasBranch &&
                      !winnerChain && (
                        <button
                          type="button"
                          className="cmp-button"
                          onClick={() => setWinnerChain(true)}
                        >
                          Show winner chains
                        </button>
                      )
                    }
                  >
                    <WinnerChains evidence={evidence} shown={winnerChain} hasBranch={hasBranch} />
                  </CardFrame>
                  <PairwisePanel
                    reading={evidence.metric}
                    nRead={evidence.subjects.filter((c) => c.n_cells > 0).length}
                    names={names}
                  />
                  <Readings evidence={evidence} />
                </>
              )}

              <CardFrame
                title="Measured edits"
                headingTag="h2"
                actions={
                  !ranking && (
                    <button type="button" className="cmp-button" onClick={() => setRanking(true)}>
                      Compute ranking
                    </button>
                  )
                }
              >
                <Ranking evidence={evidence} nSubjects={selected.length} />
              </CardFrame>
            </>
          )}
        </div>
    </div>
  );
}

// What each scoring mask did to its branch. Every field is served — including the caveat, which is a
// FACT about what a lens can and cannot answer and so has one owner on the server rather than a
// sentence each surface remembers to restate.
function Scenarios({ evidence }: { evidence: Evidence }) {
  const masked = evidence.subjects.filter((s) => s.scenario !== null);
  if (masked.length === 0) return null;
  return (
    <CardFrame title="What the scoring mask changed" headingTag="h2">
      {masked.map((s) => {
        const sc = s.scenario;
        if (!sc) return null;
        return (
          <div key={s.key}>
            <p className={sc.first_divergent_round !== null ? "l4-warn" : "l4-lede"}>
              <strong>{s.label}</strong> under <code>{s.mask?.lens}</code>:{" "}
              {/* One fact, not two: the chain ends AT the parting, so a branch cannot take a
                  different route and converge back — there is no route after it to read. */}
              {sc.winner_changed ? (
                <>
                  parts from the record at round {sc.first_divergent_round}, where it would have
                  crowned <code>{sc.scenario_winner_id?.slice(0, 8)}</code> instead of{" "}
                  <code>{sc.recorded_winner_id?.slice(0, 8)}</code>.
                </>
              ) : (
                <>never parts from the record.</>
              )}{" "}
              {sc.invariant_rounds} of {sc.total_rounds} round
              {sc.total_rounds === 1 ? "" : "s"} unchanged; the head reads over{" "}
              {sc.n_samples_scored} sample{sc.n_samples_scored === 1 ? "" : "s"}.
            </p>
            <p className="l4-note">{sc.note}</p>
          </div>
        );
      })}
    </CardFrame>
  );
}

// What the chart above is of, and — the half that matters — what it could not read. A subject
// with unscorable cells is named rather than left as a shorter bar. Rendered only where the metric
// read something; the absence case is one block below, not four paragraphs of the same fact.
//
// It reads the VIEW because the two chart families answer over different cells: the cell-wise
// forms plot every cell any subject reached, while Merged brackets each subject over its own.
// One sentence for both put the shared axis's denominator on a number that never used it — and
// they differ exactly when a subject came up short, which is when the operator is looking.
function MetricLede({
  evidence,
  view,
  names,
}: {
  evidence: Evidence;
  view: CompareView;
  names: ReadonlyMap<string, string>;
}) {
  const m = evidence.metric;
  const n = m.scored_cells.length;
  const missing = unreadable(evidence.subjects, names);
  const shared = `${n} cell${n === 1 ? "" : "s"}`;
  return (
    <>
      <p className="l4-lede">
        {m.spec.label} — {m.spec.description}{" "}
        {view === "merged"
          ? `Each subject is merged over its own cells, counted on its row; ${shared} are shared by all of them.`
          : `Plotted over every one of the ${m.covered_cells.length} cell(s) any selected subject reached; ${shared} are shared by all of them, which is what the pairs and the variance split are over.`}
      </p>
      {m.spec.higher_is_better === null && (
        <p className="l4-note">
          A composed metric has no direction the server can name — whether higher is better here is
          yours to know.
        </p>
      )}
      {missing && (
        <p className="l4-note">
          Cells measured but unreadable here: {missing}. Absent from the plot, never zero.
        </p>
      )}
    </>
  );
}

// Which subjects came up short, and on how many cells. Shared by both paths below because they
// answer the same question — and the FILTER is the point: without it the unavailable path lists a
// subject as "abc123 (0)" under a heading that says its cells were unreadable.
function unreadable(
  rows: readonly SubjectReading[],
  names: ReadonlyMap<string, string>,
): string {
  return rows
    .filter((r) => r.unscorable_cells.length > 0)
    .map((r) => `${names.get(r.key) ?? r.key} (${r.unscorable_cells.length})`)
    .join(", ");
}

// The whole of what a selection this metric cannot read has to say. It replaced a pile — an empty
// chart, a legend for bars that do not exist, a lede claiming to plot zero cells, and a
// per-subject tally — each restating one fact in different words.
function MetricUnavailable({
  evidence,
  names,
}: {
  evidence: Evidence;
  names: ReadonlyMap<string, string>;
}) {
  const m = evidence.metric;
  return (
    <>
      <p className="l4-empty">
        No subject in this selection can be read as <strong>{m.spec.label}</strong>.{" "}
        {m.spec.description}
      </p>
      <p className="l4-note">
        Cells measured but unreadable here: {unreadable(evidence.subjects, names)}. Absent, never
        zero — pick another metric to compare these subjects.
      </p>
    </>
  );
}

// The branch standing behind each channel — the winner chain from its origin to its head, each
// point read on ITS OWN cells. The rows are drawn by `EvidenceCharts`' Merged view, on one scale
// with the heads; this card says what the walk costs and what the chain means.
function WinnerChains({
  evidence,
  shown,
  hasBranch,
}: {
  evidence: Evidence;
  shown: boolean;
  hasBranch: boolean;
}) {
  if (!hasBranch) {
    return (
      <p className="l4-lede">
        Every channel here is a campaign, which is its origin — nothing precedes it. Pick a branch
        or a searchpoint to see the chain that led to it.
      </p>
    );
  }
  if (!shown) {
    return (
      <p className="l4-lede">
        Not fetched. Each channel above reads one searchpoint; a winner chain opens every round
        document its branch elected on, which is why it waits for a press.
      </p>
    );
  }
  const points = evidence.subjects.reduce((n, s) => n + (s.winner_chain?.length ?? 0), 0);
  const parted = evidence.subjects.some((s) => s.scenario?.winner_changed);
  return (
    <p className="l4-lede">
      {points} point(s) across {evidence.subjects.filter((s) => s.winner_chain).length} branch(es),
      shown under their heads in the <strong>Merged</strong> view. Each point is read on the cells
      that round actually scored — the subsets move between rounds, so a chain drawn on one shared
      axis would redraw earlier rounds on evidence they never had.
      {parted ? (
        <>
          {" "}
          A masked chain <strong>ends at the round it parts</strong> from the record: past it the
          run would have stood on a parent it never had, so there is nothing measured to draw.
        </>
      ) : null}
    </p>
  );
}

function Readings({ evidence }: { evidence: Evidence }) {
  const v = evidence.variance;
  const p = evidence.power;
  const oc = evidence.order_confound;
  // Every number below is a spread or an SD of the SELECTED metric's own cell values, so it
  // carries that metric's unit — on `usd` the roster table read `$0.4200` while this card read
  // `+0.420` for the same quantity.
  const unit = evidence.metric.spec.unit;
  return (
    <CardFrame title="What this selection can settle" headingTag="h2">
      {evidence.replicates.map((r) => (
        <p className="l4-note" key={r.arm_id}>
          Arm <code>{r.arm_id.slice(0, 8)}</code> ran {r.campaign_ids.length} times, spread{" "}
          {fmtMetricValue(unit, r.level_spread)}.{" "}
          {r.n_instruments === 1 ? (
            <>
              A <strong>replicate</strong> — that spread is noise, not an effect.
            </>
          ) : (
            <>
              <strong>Not a replicate</strong>: those runs span {r.n_instruments} measurement
              identities, so the arm was held while the instrument moved. Read that spread as
              engine drift, and expect no cell of theirs to have replayed.
            </>
          )}
        </p>
      ))}
      {!v ? (
        <p className="l4-empty">
          The variance split needs two subjects sharing two cells. Subjects on different datasets
          never share one, which is why a mixed selection stops here.
        </p>
      ) : (
        <>
          {/* The three-way split only. `null_subject_scatter` is the INTERPRETATION of the
              subject row and the lede below states it with its meaning — a second, bare copy in
              this list showed the operator one number twice in one card. */}
          <dl className="l4-readings">
            <div>
              <dt>cell effect</dt>
              <dd>{fmtMetricValue(unit, v.cell_effect_sd)}</dd>
            </div>
            <div>
              <dt>subject effect</dt>
              <dd className={cx(v.subject_sd_below_noise && "l4-dim")}>
                {fmtMetricValue(unit, v.subject_effect_sd)}
              </dd>
            </div>
            <div>
              <dt>residual</dt>
              <dd>{fmtMetricValue(unit, v.residual_sd)}</dd>
            </div>
          </dl>
          <p className="l4-lede">
            Over the {v.n_cells} cell{v.n_cells === 1 ? "" : "s"} all {v.n_subjects} subjects
            measured. Under the null a subject mean still scatters by{" "}
            {fmtMetricValue(unit, v.null_subject_scatter)} —{" "}
            {v.subject_sd_below_noise ? (
              <strong>so nothing here is distinguishable from noise.</strong>
            ) : (
              <>the subjects differ by more than noise alone would produce.</>
            )}
          </p>
        </>
      )}
      {p && (
        <p className="l4-lede">
          At {p.cells_per_subject} cells/subject the paired SE is{" "}
          {fmtMetricValue(unit, p.paired_se)}, so the smallest detectable effect is{" "}
          {fmtMetricValue(unit, p.min_detectable_effect)}. The widest gap on the roster is{" "}
          {fmtMetricValue(unit, p.largest_subject_gap)}
          {p.cells_for_largest_gap !== null && (
            <> — resolving it would take ~{p.cells_for_largest_gap} cells per subject</>
          )}
          .
        </p>
      )}
      {oc?.level_vs_order != null && (
        <p className={cx("l4-lede", oc.order_confounded && "l4-warn")}>
          Run-order confound: value vs order ρ {fmtSigned(oc.level_vs_order, 2)}
          {oc.order_confounded && (
            <>
              {" "}
              — the roster&rsquo;s ordering is also its chronology, so which subject it is and
              when it ran cannot be told apart
            </>
          )}
          .
        </p>
      )}
    </CardFrame>
  );
}

function Ranking({ evidence, nSubjects }: { evidence: Evidence; nSubjects: number }) {
  const m = evidence.metric;
  if (!evidence.ranking_computed) {
    return (
      <p className="l4-lede">
        Not computed. Everything above reads one searchpoint per channel; this walks every round
        document of all {nSubjects} — the widest read here, which is why it waits for a press. It
        ranks edits against their own campaign&rsquo;s origin, so only campaign channels feed it.
      </p>
    );
  }
  if (evidence.edits.length === 0) {
    return (
      <p className="l4-empty">
        No scored edits in this selection. One needs its campaign&rsquo;s round-0 origin plus at
        least one later round to compare against — and a campaign channel to be ticked at all.
      </p>
    );
  }
  return (
    <div className="l4-table-wrap">
      <table className="l4-table">
        <thead>
          <tr>
            <th scope="col">#</th>
            <th scope="col">edit</th>
            <th scope="col">{m.spec.label} over its origin · 95% CI</th>
            <th scope="col">cells / n</th>
            <th scope="col">seen</th>
          </tr>
        </thead>
        <tbody>
          {evidence.edits.map((row, i) => (
            <tr className="l4-row" key={row.state_hash}>
              <td className="l4-rank">{i + 1}</td>
              <td className="l4-label" title={row.label}>
                <code>{row.state_hash}</code> {row.label}
              </td>
              <td className={cx("l4-effect", effectTone(row.ci_lo, row.ci_hi))}>
                <span className="l4-effect-mean">
                  {fmtMetricValue(m.spec.unit, row.anchor_effect)}
                </span>
                <span className="l4-effect-ci">
                  {fmtMetricInterval(m.spec.unit, row.ci_lo, row.ci_hi)}
                </span>
              </td>
              <td className="l4-num">
                {row.n_cells}
                <span className="l4-dim"> / {row.n_measurements}</span>
              </td>
              <td className="l4-num">{row.provenance.length}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="l4-lede">
        An interval spanning zero is the ordinary outcome on a small panel — the ranking orders
        these, it does not endorse them. To settle one, deepen it with <code>verify</code> rather
        than repeating it.
      </p>
    </div>
  );
}
