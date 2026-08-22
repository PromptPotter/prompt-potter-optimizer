"use client";
// The Compare tab — pick any campaigns from any datasets and read what they jointly say.
//
// It exists because the question "I ran this four times, now what?" is about a SET, and the only
// surface that answered it lived inside one campaign's dashboard, gated to the self-optimizing
// loop. There is no L4 gate here and no dataset scope: an ordinary campaign and a pp-self one
// take the same path, and a selection that spans datasets is allowed and then reported on.
//
// Cheap reads auto-load on any selection change — including the metric, every interval and every
// pairwise test, which are arithmetic over values the roster read already had in hand. The edit
// ranking is the one walk expensive enough to sit behind a press (see `useEvidence`).

import { useCallback, useMemo, useState } from "react";
import type { CampaignReading, Evidence } from "@/lib/api";
import { CardFrame, SegmentedControl, Toolbar, ToolbarSep } from "@/components/ui";
import { cx } from "@/lib/cx";
import { useWorkspace } from "@/lib/workspace";
import { useEvidence } from "@/lib/hooks/useEvidence";
import {
  effectTone,
  fmtMetricInterval,
  fmtMetricValue,
  fmtSigned,
  shortId,
} from "@/lib/format";
import { CampaignPicker } from "./CampaignPicker";
import { EvidenceCharts, SeriesLegend, type CompareView } from "./EvidenceCharts";
import { isCustomMetric, MetricExpression, MetricPicker } from "./MetricPicker";
import { PairwisePanel } from "./PairwisePanel";

const VIEWS: readonly { value: CompareView; label: string; title: string }[] = [
  { value: "grouped", label: "Grouped", title: "One bar per campaign, grouped by cell" },
  { value: "overlaid", label: "Stacked", title: "One bar per cell, campaigns sharing it" },
  { value: "lines", label: "Lines", title: "One line per campaign across the shared cells" },
  {
    value: "merged",
    label: "Merged",
    title: "Cells merged: one estimate per campaign, with its 95% interval",
  },
];

// The SENTENCE is served (`Comparability.note`); a per-reason text map here would be a second
// copy free to drift out of step with the terminal's. Only the tone is a rendering choice, and
// it reads `verdict`, whose `null` is UNKNOWN and never a yes.
function comparabilityTone(verdict: boolean | null): string {
  return verdict === true ? "l4-note" : "l4-warn";
}

export function ComparePane() {
  const { campaigns } = useWorkspace();
  const [selected, setSelected] = useState<readonly string[]>([]);
  const [view, setView] = useState<CompareView>("grouped");
  const [ranking, setRanking] = useState(false);
  // ONE opaque selector — a catalogue key or a composed `expr:…`, both the server's spellings.
  // Empty means "unset": `fetchEvidence` then omits the query param and the SERVER picks its own
  // default, so the browser never needs a second copy of what that default is.
  const [metric, setMetric] = useState("");
  const { evidence, loading, error, invalidMetric } = useEvidence(selected, ranking, metric);

  // Turning the selection over invalidates the ranking press — the walk was for a different
  // set — and the metric with it: the catalogue is per-selection, so one campaign's channel may
  // not be answerable by the next lot, and holding a stale pick 400s on the very next read.
  const reset = useCallback(() => {
    setRanking(false);
    setMetric("");
  }, []);

  const toggle = useCallback((id: string) => {
    reset();
    setSelected((prev) => (prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]));
  }, [reset]);
  const selectMany = useCallback(
    (ids: readonly string[]) => {
      reset();
      setSelected((prev) => [...new Set([...prev, ...ids])]);
    },
    [reset],
  );
  const clear = useCallback(() => {
    reset();
    setSelected([]);
  }, [reset]);

  const comparability = evidence?.comparability ?? null;
  // Whether the selected metric read ANYTHING. Everything below the picker describes a number
  // that then does not exist, so it stays silent rather than restating the same absence four ways.
  const readable = !!evidence?.campaigns.some((c) => c.n_cells > 0);

  return (
    <div className="content" id="content-compare">
      <div className="cmp-layout">
        <CardFrame title="Campaigns" headingTag="h2">
          <CampaignPicker
            campaigns={campaigns}
            selected={selected}
            onToggle={toggle}
            onSelectDataset={selectMany}
            onClear={clear}
          />
        </CardFrame>

        <div className="cmp-main">
          {selected.length === 0 ? (
            <CardFrame title="Compare" headingTag="h2">
              <p className="l4-lede">
                Pick two or more campaigns. They may come from different datasets — the read says
                what that costs rather than refusing it.
              </p>
            </CardFrame>
          ) : error ? (
            <CardFrame title="Compare" headingTag="h2">
              <p className="l4-warn">Could not read the selection: {error}</p>
            </CardFrame>
          ) : !evidence || loading ? (
            <CardFrame title="Compare" headingTag="h2">
              <p className="l4-empty">Reading {selected.length} campaign(s)…</p>
            </CardFrame>
          ) : (
            <>
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
                {/* Selected, and not in anything below. A campaign that thins the selection in
                    silence is the campaign-level twin of scoring an unread cell as zero. */}
                {evidence.unread_campaigns.length > 0 && (
                  <p className="l4-note">
                    Selected but not read: {evidence.unread_campaigns.map(shortId).join(", ")}.
                    Each has no scored round-0 origin yet, so it is absent from every number here
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
                    <SeriesLegend evidence={evidence} />
                    <MetricLede evidence={evidence} view={view} />
                  </>
                ) : (
                  <MetricUnavailable evidence={evidence} />
                )}
              </CardFrame>

              {readable && (
                <>
                  <PairwisePanel
                    reading={evidence.metric}
                    nRead={evidence.campaigns.filter((c) => c.n_cells > 0).length}
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
                <Ranking evidence={evidence} nCampaigns={selected.length} />
              </CardFrame>
            </>
          )}
        </div>
      </div>
    </div>
  );
}

// What the chart above is of, and — the half that matters — what it could not read. A campaign
// with unscorable cells is named rather than left as a shorter bar. Rendered only where the metric
// read something; the absence case is one block below, not four paragraphs of the same fact.
//
// It reads the VIEW because the two chart families answer over different cells: the cell-wise
// forms plot `scored_cells`, the intersection, while Merged brackets each campaign over its own.
// One sentence for both put the shared axis's denominator on a number that never used it — and
// they differ exactly when a campaign came up short, which is when the operator is looking.
function MetricLede({ evidence, view }: { evidence: Evidence; view: CompareView }) {
  const m = evidence.metric;
  const n = m.scored_cells.length;
  const missing = unreadable(evidence.campaigns);
  const shared = `${n} cell${n === 1 ? "" : "s"}`;
  return (
    <>
      <p className="l4-lede">
        {m.spec.label} — {m.spec.description}{" "}
        {view === "merged"
          ? `Each campaign is merged over its own cells, counted on its row; ${shared} are shared by all of them.`
          : n > 0
            ? `Plotted over the ${shared} every selected campaign scored under it.`
            : "No cell was scored by every selected campaign, so only the merged reading compares them."}
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

// Which campaigns came up short, and by how many cells. Shared by both paths below because they
// answer the same question — and the FILTER is the point: without it the unavailable path lists a
// campaign as "abc123 (0)" under a heading that says its cells were unreadable.
function unreadable(rows: readonly CampaignReading[]): string {
  return rows
    .filter((r) => r.n_unscorable > 0)
    .map((r) => `${shortId(r.campaign_id)} (${r.n_unscorable})`)
    .join(", ");
}

// The whole of what a selection this metric cannot read has to say. It replaced a pile — an empty
// chart, a legend for bars that do not exist, a lede claiming to plot zero cells, and a per-campaign
// tally — each restating one fact in different words.
function MetricUnavailable({ evidence }: { evidence: Evidence }) {
  const m = evidence.metric;
  return (
    <>
      <p className="l4-empty">
        No campaign in this selection can be read as <strong>{m.spec.label}</strong>.{" "}
        {m.spec.description}
      </p>
      <p className="l4-note">
        Cells measured but unreadable here: {unreadable(evidence.campaigns)}. Absent, never zero —
        pick another metric to compare these campaigns.
      </p>
    </>
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
          Arm <code>{r.arm_id.slice(0, 8)}</code> ran {r.campaign_ids.length} times — a{" "}
          <strong>replicate</strong>, spread {fmtMetricValue(unit, r.level_spread)}. That spread is
          noise, not an effect.
        </p>
      ))}
      {!v ? (
        <p className="l4-empty">
          The variance split needs two campaigns sharing two cells. Campaigns on different
          datasets never share one, which is why a mixed selection stops here.
        </p>
      ) : (
        <>
          {/* The three-way split only. `null_arm_scatter` is the INTERPRETATION of the arm
              row and the lede below states it with its meaning — a second, bare copy in this
              list showed the operator one number twice in one card. */}
          <dl className="l4-readings">
            <div>
              <dt>cell effect</dt>
              <dd>{fmtMetricValue(unit, v.cell_effect_sd)}</dd>
            </div>
            <div>
              <dt>arm effect</dt>
              <dd className={cx(v.arm_sd_below_noise && "l4-dim")}>
                {fmtMetricValue(unit, v.arm_effect_sd)}
              </dd>
            </div>
            <div>
              <dt>residual</dt>
              <dd>{fmtMetricValue(unit, v.residual_sd)}</dd>
            </div>
          </dl>
          <p className="l4-lede">
            Over the {v.n_cells} cell{v.n_cells === 1 ? "" : "s"} all {v.n_arms} campaigns
            measured. Under the null an arm mean still scatters by{" "}
            {fmtMetricValue(unit, v.null_arm_scatter)} —{" "}
            {v.arm_sd_below_noise ? (
              <strong>so nothing here is distinguishable from noise.</strong>
            ) : (
              <>the arms differ by more than noise alone would produce.</>
            )}
          </p>
        </>
      )}
      {p && (
        <p className="l4-lede">
          At {p.cells_per_arm} cells/arm the paired SE is {fmtMetricValue(unit, p.paired_se)}, so
          the smallest detectable effect is {fmtMetricValue(unit, p.min_detectable_effect)}. The
          widest gap on the roster is {fmtMetricValue(unit, p.largest_arm_gap)}
          {p.cells_for_largest_gap !== null && (
            <> — resolving it would take ~{p.cells_for_largest_gap} cells per arm</>
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
              — the roster&rsquo;s ordering is also its chronology, so which campaign it is and
              when it ran cannot be told apart
            </>
          )}
          .
        </p>
      )}
    </CardFrame>
  );
}

function Ranking({ evidence, nCampaigns }: { evidence: Evidence; nCampaigns: number }) {
  const m = evidence.metric;
  if (!evidence.ranking_computed) {
    return (
      <p className="l4-lede">
        Not computed. Everything above reads one origin per campaign; this walks every round
        document of all {nCampaigns} — the only expensive read here, which is why it waits for a
        press.
      </p>
    );
  }
  if (evidence.edits.length === 0) {
    return (
      <p className="l4-empty">
        No scored edits in this selection. One needs its campaign&rsquo;s round-0 origin plus at
        least one later round to compare against.
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
