"use client";

import { useEffect, useMemo, useState } from "react";
import type { Evidence, SubjectReading } from "@/lib/api";
import { CardFrame, SegmentedControl, Toolbar, ToolbarSep } from "@/components/ui";
import { cx } from "@/lib/cx";
import { useCompareSelection } from "@/lib/compare-selection";
import { readPaired } from "@/lib/derivations";
import { invalidateReads } from "@/lib/read-cache";
import { useRegistry } from "@/lib/registry";
import { useEvidence } from "@/lib/hooks/useEvidence";
import {
  sideTone,
  fmtMetricInterval,
  fmtMetricValue,
  fmtSigned,
  shortId,
  verdictTone,
} from "@/lib/format";
import { maskedSubject } from "@/lib/api/reads";
import { phaseWalks } from "@/lib/run-phase";
import { PairedLift } from "@/components/shell/PairedLift";
import { ChannelCards } from "./ChannelCards";
import { Coverage, EvidenceCharts, SeriesLegend, type CompareView } from "./EvidenceCharts";
import { ChannelMask } from "./ChannelMask";
import { FactorGrid } from "./FactorGrid";
import { NO_EDITS, ScenarioEditsProvider, type ScenarioEdits } from "./config-edit";
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

const LIVE_EVIDENCE_MS = 30000;

function channelNames(subjects: readonly SubjectReading[]): ReadonlyMap<string, string> {
  return new Map(
    subjects.map((s) => [s.key, s.kind === "campaign" ? shortId(s.label) : s.label]),
  );
}

export function ComparePane() {
  const { channels, subjects: selected, replace } = useCompareSelection();
  const [view, setView] = useState<CompareView>("grouped");
  // The bare address, not the key: applying a mask changes the key, which would close the form.
  const [masking, setMasking] = useState<string | null>(null);
  const [ranking, setRanking] = useState(false);
  const [winnerChain, setWinnerChain] = useState(false);
  const [config, setConfig] = useState(true);
  const [edits, setEdits] = useState<ScenarioEdits>(NO_EDITS);
  const editing = useMemo(() => ({ edits, setEdits }), [edits]);
  // "" omits the query param, so the server picks its own default.
  const [metric, setMetric] = useState("");
  const [grid, setGrid] = useState("");
  const { forest } = useRegistry();
  const runs = useMemo(
    () => new Map(forest.flatMap((o) => o.runs).map((r) => [r.campaign.campaign_id, r])),
    [forest],
  );
  const compared = channels.flatMap((c) => runs.get(c.rootCampaignId)?.line ?? []);
  const moved = compared
    .map((a) => `${a.holder.cycle_id}:${a.rounds_closed}:${a.run_phase}`)
    .join("|");
  useEffect(() => invalidateReads("evidence"), [moved]);
  const running = compared.some((a) => phaseWalks(a.run_phase));
  const { evidence, loading, error, invalidMetric } = useEvidence(
    selected,
    running ? LIVE_EVIDENCE_MS : null,
    { ranking, winnerChain, config, metric, grid },
  );

  // Keyed on campaigns, so re-pointing a channel keeps the metric; a stale metric pick 400s.
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

  const comparability = evidence?.comparability ?? null;
  const readable = !!evidence?.subjects.some((c) => c.n_cells > 0);
  const names = useMemo(() => channelNames(evidence?.subjects ?? []), [evidence?.subjects]);
  const maskTarget =
    evidence?.subjects.find((s) => maskedSubject(s, {}) === masking) ?? null;
  if (masking !== null && evidence && !loading && maskTarget === null) setMasking(null);
  const hasBranch = !!evidence?.subjects.some((s) => s.kind !== "campaign");

  return (
    <div className="content" id="content-compare">
        <div className="cmp-main">
          {selected.length === 0 ? (
            <CardFrame title="Compare" headingTag="h2">
              <p className="note-lede">
                {/* "the campaign list", not "the sidebar": on a phone there is no sidebar. */}
                Tick <strong>▢</strong> beside two or more campaigns in the campaign list. Each lands on
                its own winner; open a channel&rsquo;s lineage to walk its cladogram — click any
                searchpoint to move that channel onto it, or to put it on the board beside the
                winner. They may come from different datasets — the read says what that costs
                rather than refusing it.
              </p>
            </CardFrame>
          ) : error ? (
            <CardFrame title="Compare" headingTag="h2">
              <p className="note-warn">Could not read the selection: {error}</p>
            </CardFrame>
          ) : !evidence || loading ? (
            <CardFrame title="Compare" headingTag="h2">
              <p className="note-empty">Reading {selected.length} channel(s)…</p>
            </CardFrame>
          ) : (
            <ScenarioEditsProvider value={editing}>
              <ChannelCards evidence={evidence} runs={runs} />

              {config ? (
                <SearchpointCards evidence={evidence} loading={loading} />
              ) : (
                <CardFrame title="How these searchpoints are configured" headingTag="h2">
                  <p className="note-lede">
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
                {invalidMetric && !isCustomMetric(metric) && (
                  <p className="note-warn">{invalidMetric}</p>
                )}
                {evidence.unread_subjects.length > 0 && (
                  <p className="note-info">
                    Selected but not read: {evidence.unread_subjects.join(", ")}. Each has nothing
                    measured at the point it addresses, so it is absent from every number here
                    rather than counted as a low one.
                  </p>
                )}
                {readable ? (
                  <>
                    {comparability && (
                      <p className={verdictTone(comparability.verdict)}>
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
                        // Keyed: unkeyed, the next channel opens holding this one's local draft.
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
                  <FactorGrid evidence={evidence} grid={grid} onGrid={setGrid} />
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
            </ScenarioEditsProvider>
          )}
        </div>
    </div>
  );
}

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
            <p className={sc.first_divergent_round !== null ? "note-warn" : "note-lede"}>
              <strong>{s.label}</strong> under <code>{s.mask?.lens}</code>:{" "}
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
            <p className="note-info">{sc.note}</p>
          </div>
        );
      })}
    </CardFrame>
  );
}

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
      <p className="note-lede">
        {m.spec.label} — {m.spec.description} Graded by <code>{evidence.scorer_id}</code>.{" "}
        {view === "merged"
          ? `Each subject is merged over its own cells, counted on its row; ${shared} are shared by all of them.`
          : `Plotted over every one of the ${m.covered_cells.length} cell(s) any selected subject reached; ${shared} are shared by all of them, which is what the pairs and the variance split are over.`}
      </p>
      {m.spec.higher_is_better === null && (
        <p className="note-info">
          A composed metric has no direction the server can name — whether higher is better here is
          yours to know.
        </p>
      )}
      {missing && (
        <p className="note-info">
          Cells measured but unreadable here: {missing}. Absent from the plot, never zero.
        </p>
      )}
    </>
  );
}

function unreadable(
  rows: readonly SubjectReading[],
  names: ReadonlyMap<string, string>,
): string {
  return rows
    .filter((r) => r.unscorable_cells.length > 0)
    .map((r) => `${names.get(r.key) ?? r.key} (${r.unscorable_cells.length})`)
    .join(", ");
}

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
      <p className="note-empty">
        No subject in this selection can be read as <strong>{m.spec.label}</strong>.{" "}
        {m.spec.description}
      </p>
      <p className="note-info">
        Cells measured but unreadable here: {unreadable(evidence.subjects, names)}. Absent, never
        zero — pick another metric to compare these subjects.
      </p>
    </>
  );
}

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
      <p className="note-lede">
        Every channel here is a campaign, which is its origin — nothing precedes it. Pick a branch
        or a searchpoint to see the chain that led to it.
      </p>
    );
  }
  if (!shown) {
    return (
      <p className="note-lede">
        Not fetched. Each channel above reads one searchpoint; a winner chain opens every round
        file its branch elected on, which is why it waits for a press.
      </p>
    );
  }
  const points = evidence.subjects.reduce((n, s) => n + (s.winner_chain?.length ?? 0), 0);
  const parted = evidence.subjects.some((s) => s.scenario?.winner_changed);
  return (
    <p className="note-lede">
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
  const unit = evidence.metric.spec.unit;
  return (
    <CardFrame title="What this selection can settle" headingTag="h2">
      {evidence.replicates.map((r) => (
        <p className="note-info" key={r.arm_id}>
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
        <p className="note-empty">
          The variance split needs two subjects sharing two cells. Subjects on different datasets
          never share one, which is why a mixed selection stops here.
        </p>
      ) : (
        <>
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
          <p className="note-lede">
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
        <p className="note-lede">
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
        <p className={cx("note-lede", oc.order_confounded && "note-warn")}>
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
      <p className="note-lede">
        Not computed. Everything above reads one searchpoint per channel; this walks every round
        file of all {nSubjects} — the widest read here, which is why it waits for a press. It
        ranks each SEARCHPOINT against its own campaign&rsquo;s origin, so only campaign channels
        feed it and nothing pools across two of them.
      </p>
    );
  }
  if (evidence.edits.length === 0) {
    return (
      <p className="note-empty">
        No scored edits in this selection. One needs its campaign&rsquo;s round-0 origin plus a
        second searchpoint measured on cells that origin also scored — and a campaign channel to
        be ticked at all.
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
            <th scope="col">cells</th>
            <th scope="col">seen</th>
          </tr>
        </thead>
        <tbody>
          {/* Keyed on both halves: two campaigns can share one `sp_hash`. */}
          {evidence.edits.map((row, i) => {
            const pair = readPaired(row.reading);
            const lift = pair.read ? pair.lift.estimate : null;
            return (
              <tr className="l4-row" key={`${row.campaign_id}/${row.sp_hash}`}>
                <td className="l4-rank">{lift === null ? "—" : i + 1}</td>
                <td className="l4-label" title={`${row.label} · ${row.campaign_id}`}>
                  <code>{row.sp_hash}</code> {row.label}
                  <span className="l4-dim"> {shortId(row.campaign_id)}</span>
                </td>
                {lift === null ? (
                  <td className="l4-dim">
                    <PairedLift reading={row.reading} unread="sentence" />
                  </td>
                ) : (
                  <td className={cx("l4-effect", sideTone(lift.side))}>
                    <span className="l4-effect-mean">{fmtMetricValue(m.spec.unit, lift.value)}</span>
                    <span className="l4-effect-ci">
                      {fmtMetricInterval(m.spec.unit, lift.ci_lo, lift.ci_hi)}
                    </span>
                  </td>
                )}
                <td className="l4-num">{pair.read ? pair.cells : "—"}</td>
                <td className="l4-num">{row.provenance.length}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
      <p className="note-lede">
        An interval spanning zero is the ordinary outcome on a small panel — the ranking orders
        these, it does not endorse them. To settle one, deepen it with <code>verify</code> rather
        than repeating it.
      </p>
    </div>
  );
}
