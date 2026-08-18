"use client";
// The Compare tab — pick any campaigns from any datasets and read what they jointly say.
//
// It exists because the question "I ran this four times, now what?" is about a SET, and the only
// surface that answered it lived inside one campaign's dashboard, gated to the self-optimizing
// loop. There is no L4 gate here and no dataset scope: an ordinary campaign and a pp-self one
// take the same path, and a selection that spans datasets is allowed and then reported on.
//
// Cheap reads auto-load on any selection change; the edit ranking is the one expensive walk and
// sits behind a press (see `useEvidence`).

import { useCallback, useMemo, useState } from "react";
import type { Evidence } from "@/lib/api";
import { CardFrame, SegmentedControl } from "@/components/ui";
import { cx } from "@/lib/cx";
import { useWorkspace } from "@/lib/workspace";
import { useEvidence } from "@/lib/hooks/useEvidence";
import { CampaignPicker } from "./CampaignPicker";
import { EvidenceCharts, SeriesLegend, type CompareView } from "./EvidenceCharts";

const VIEWS: readonly { value: CompareView; label: string; title: string }[] = [
  { value: "grouped", label: "Grouped", title: "One bar per campaign, grouped by cell" },
  { value: "overlaid", label: "Stacked", title: "One bar per cell, campaigns sharing it" },
  { value: "lines", label: "Lines", title: "One line per campaign across the shared cells" },
  { value: "forest", label: "Forest", title: "Each campaign's mean level on one axis" },
];

// Keyed by `Comparability.reason`. Every one of these disqualifies or qualifies the `level`
// column, so none may render as a shrug.
const COMPARABILITY: Record<Evidence["comparability"]["reason"], { tone: string; text: string }> = {
  one_ruler: {
    tone: "l4-note",
    text: "These origins were measured on one δ ruler, so their levels are directly comparable.",
  },
  rulers_differ: {
    tone: "l4-warn",
    text:
      "These origins were measured on different δ rulers, so their absolute levels are not one " +
      "quantity. Only within-ruler comparisons hold.",
  },
  ruler_unstamped: {
    tone: "l4-warn",
    text:
      "At least one origin predates the ruler stamp, so comparability is unknown — which is not " +
      "the same as yes. Pair on cells; do not read the level column across campaigns.",
  },
  datasets_differ: {
    tone: "l4-warn",
    text:
      "This selection spans several datasets, which measure different things. The levels are not " +
      "one quantity and no pairing rescues them — the roster and spend still compare, the numbers do not.",
  },
};

function fmt(n: number, places = 3): string {
  return (n >= 0 ? "+" : "") + n.toFixed(places);
}

export function ComparePane() {
  const { campaigns } = useWorkspace();
  const [selected, setSelected] = useState<readonly string[]>([]);
  const [view, setView] = useState<CompareView>("grouped");
  const [ranking, setRanking] = useState(false);
  const { evidence, loading, error } = useEvidence(selected, ranking);

  const toggle = useCallback((id: string) => {
    // Turning the selection over invalidates a ranking press — the walk was for a different set.
    setRanking(false);
    setSelected((prev) => (prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]));
  }, []);
  const selectMany = useCallback((ids: readonly string[]) => {
    setRanking(false);
    setSelected((prev) => [...new Set([...prev, ...ids])]);
  }, []);
  const clear = useCallback(() => {
    setRanking(false);
    setSelected([]);
  }, []);

  const comparability = useMemo(
    () => (evidence ? COMPARABILITY[evidence.comparability.reason] : null),
    [evidence],
  );

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
                  <SegmentedControl
                    options={VIEWS}
                    value={view}
                    onChange={setView}
                    ariaLabel="Chart form"
                  />
                }
              >
                {comparability && <p className={comparability.tone}>{comparability.text}</p>}
                <EvidenceCharts evidence={evidence} view={view} />
                <SeriesLegend evidence={evidence} />
                <p className="l4-lede">
                  Plotted over the {evidence.shared_cells.length} cell
                  {evidence.shared_cells.length === 1 ? "" : "s"} every selected campaign measured,
                  in the measurand&rsquo;s own units — never composite fitness, which each
                  campaign&rsquo;s own formula defines differently.
                </p>
              </CardFrame>

              <Readings evidence={evidence} />

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

function Readings({ evidence }: { evidence: Evidence }) {
  const v = evidence.variance;
  const p = evidence.power;
  const oc = evidence.order_confound;
  return (
    <CardFrame title="What this selection can settle" headingTag="h2">
      {evidence.replicates.map((r) => (
        <p className="l4-note" key={r.arm_id}>
          Arm <code>{r.arm_id.slice(0, 8)}</code> ran {r.campaign_ids.length} times — a{" "}
          <strong>replicate</strong>, spread {fmt(r.level_spread)}. That spread is noise, not an
          effect.
        </p>
      ))}
      {!v ? (
        <p className="l4-empty">
          The variance split needs two campaigns sharing two cells. Campaigns on different
          datasets never share one, which is why a mixed selection stops here.
        </p>
      ) : (
        <>
          <dl className="l4-readings">
            <div>
              <dt>cell effect</dt>
              <dd>{v.cell_effect_sd.toFixed(3)}</dd>
            </div>
            <div>
              <dt>arm effect</dt>
              <dd className={cx(v.arm_sd_below_noise && "l4-dim")}>
                {v.arm_effect_sd.toFixed(3)}
              </dd>
            </div>
            <div>
              <dt>residual</dt>
              <dd>{v.residual_sd.toFixed(3)}</dd>
            </div>
            <div>
              <dt>noise floor</dt>
              <dd>{v.null_arm_scatter.toFixed(3)}</dd>
            </div>
          </dl>
          <p className="l4-lede">
            Over the {v.n_cells} cell{v.n_cells === 1 ? "" : "s"} all {v.n_arms} campaigns
            measured. Under the null an arm mean still scatters by {v.null_arm_scatter.toFixed(3)}{" "}
            —{" "}
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
          At {p.cells_per_arm} cells/arm the paired SE is {p.paired_se.toFixed(3)}, so the smallest
          detectable effect is {fmt(p.min_detectable_effect)}. The widest gap on the roster is{" "}
          {fmt(p.largest_arm_gap)}
          {p.cells_for_largest_gap !== null && (
            <> — resolving it would take ~{p.cells_for_largest_gap} cells per arm</>
          )}
          .
        </p>
      )}
      {oc?.level_vs_order != null && (
        <p className={cx("l4-lede", oc.order_confounded && "l4-warn")}>
          Run-order confound: level vs order ρ {fmt(oc.level_vs_order, 2)}
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
            <th scope="col">effect over its origin · 95% CI</th>
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
              <td
                className={cx(
                  "l4-effect",
                  row.ci_lo > 0 ? "l4-eff-pos" : row.ci_hi < 0 ? "l4-eff-neg" : "l4-eff-flat",
                )}
              >
                <span className="l4-effect-mean">{fmt(row.anchor_effect, 4)}</span>
                <span className="l4-effect-ci">
                  [{fmt(row.ci_lo, 4)}, {fmt(row.ci_hi, 4)}]
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
