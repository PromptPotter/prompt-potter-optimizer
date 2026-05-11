"use client";
import { useEffect, useMemo, useState } from "react";
import { TERMS } from "@/lib/terms";
import { WHATIF_INLINE_META, buildRows, whatifIdentifiersInFormula, type Row } from "./meta";
import { whatifIconFor } from "./icons";
import { FitnessChart, type BarSlot } from "./FitnessChart";
import { setFitnessState, useFitnessState } from "./fitness-store";
import { parseSampleLine } from "@/lib/sample-line";
import { candidateLabel } from "@/lib/candidate-label";
import { fetchCycleFile, fetchFiles } from "@/lib/api";
import type { DashboardSnapshot } from "@/lib/poll";

interface InflightCandidate {
  idx?: number;
  label?: string;
  stats?: {
    composite_fitness?: number;
    accuracy?: number;
    evaluators?: Record<string, number>;
  };
  // Live HIT/MISS lines from LiveDashboardProjection. Populated per-sample
  // before stats lands at candidate-eval completion.
  samples?: string[];
}

interface HistoricalCandidate {
  label?: string;
  changes_description?: string;
  accuracy?: number;
  composite_fitness?: number;
  evaluators?: Record<string, number>;
  invalid?: boolean;
}

interface HistoricalRound {
  round: number;                   // canonical round_num (0 = origin, 1..N = L1)
  candidate_scores?: HistoricalCandidate[];
  origin_accuracy?: number;
}

interface Props {
  dash: DashboardSnapshot | null;
  cycleId: string | null;
  refreshKey: number;              // bumped each completed round (drives refetch)
  themeKey: string;
}

function correctedFromEvaluators(
  evaluators: Record<string, number>,
  selected: Set<string>,
  rows: Row[],
): number | null {
  let sum = 0;
  let n = 0;
  for (const sel of selected) {
    const v = evaluators[sel];
    if (v == null) continue;
    const direction = rows.find((rr) => rr.displayName === sel)?.direction ?? "high";
    sum += direction === "low" ? 1 - v : v;
    n += 1;
  }
  return n > 0 ? sum / n : null;
}

function runningFromSamples(samples: string[] | undefined): number | null {
  if (!samples || samples.length === 0) return null;
  let hits = 0;
  let total = 0;
  for (const raw of samples) {
    const p = parseSampleLine(raw);
    if (p.status === "HIT") { hits += 1; total += 1; }
    else if (p.status === "MISS") { total += 1; }
  }
  return total > 0 ? hits / total : null;
}

function ranks(lines: { key: string; v: number | null }[]): Map<string, number> {
  const sortable = lines.filter((l) => l.v != null).slice().sort((a, b) => (b.v as number) - (a.v as number));
  const m = new Map<string, number>();
  sortable.forEach((l, i) => m.set(l.key, i + 1));
  return m;
}

function pickWinner(lines: { key: string; v: number | null }[]): string | null {
  let best: string | null = null;
  let bestVal = -Infinity;
  for (const l of lines) {
    if (l.v == null) continue;
    if (l.v > bestVal) {
      bestVal = l.v;
      best = l.key;
    }
  }
  return best;
}

export function FitnessPanel({ dash, cycleId, refreshKey, themeKey }: Props) {
  // Default view: chart-only (one bar per candidate = accuracy). The
  // composite chip pairs the formula-weighted score; the what-if chip
  // opens the ablation widget below the chart. State lives in a module
  // store so swapping tabs (New Job ↔ View Results) preserves chip and
  // selection state across the FitnessPanel unmount.
  const { showComposite, showWhatIf, selected, seeded } = useFitnessState();
  const setShowComposite = (v: boolean | ((p: boolean) => boolean)) =>
    setFitnessState({ showComposite: typeof v === "function" ? v(showComposite) : v });
  const setShowWhatIf = (v: boolean | ((p: boolean) => boolean)) =>
    setFitnessState({ showWhatIf: typeof v === "function" ? v(showWhatIf) : v });
  const setSelected = (s: Set<string>) => setFitnessState({ selected: s });

  const meta = WHATIF_INLINE_META;

  // ── 1. In-flight candidates from the live dashboard
  const cr = (dash?.current_round?.nodes as Record<string, { output?: { candidates?: InflightCandidate[] } }> | undefined)?.l1_score ?? null;
  const inflightCandidates: InflightCandidate[] = cr?.output?.candidates ?? [];
  // dash.current_round.round is canonical: 0 = origin, 1..N = L1. No
  // arithmetic; the badge shows it verbatim.
  const currentRound = Number(dash?.current_round?.round ?? dash?.round ?? 0);

  // ── 2. Historical rounds — round_NNNN.json files on disk
  const [history, setHistory] = useState<HistoricalRound[]>([]);
  useEffect(() => {
    if (!cycleId) {
      setHistory([]);
      return;
    }
    let cancelled = false;
    (async () => {
      try {
        const listing = await fetchFiles(cycleId);
        const rounds = listing.entries
          .filter((e) => e.scope === "cycle" && /^rounds\/round_\d+\.json$/.test(e.path))
          .sort((a, b) => a.path.localeCompare(b.path));
        const fetched = await Promise.all(
          rounds.map(async (f) => {
            try {
              const r = await fetchCycleFile(cycleId, "cycle", f.path);
              return JSON.parse(r.content) as HistoricalRound;
            } catch {
              return null;
            }
          }),
        );
        const valid = fetched.filter((p): p is HistoricalRound => p !== null).sort(
          (a, b) => (a.round ?? 0) - (b.round ?? 0),
        );
        if (!cancelled) setHistory(valid);
      } catch {
        /* round list unreachable — fall back to in-flight only */
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [cycleId, refreshKey]);

  // ── 3. The applicable evaluator set unions every candidate we plot. The
  // origin row has no evaluators; in-flight stats and historical
  // candidate_scores both carry the full dict.
  const realApplicable = useMemo(() => {
    const set = new Set<string>();
    for (const c of inflightCandidates) {
      for (const k of Object.keys(c.stats?.evaluators ?? {})) set.add(k);
    }
    for (const h of history) {
      for (const c of h.candidate_scores ?? []) {
        for (const k of Object.keys(c.evaluators ?? {})) set.add(k);
      }
    }
    return set;
  }, [inflightCandidates, history]);

  const isPrestaging = realApplicable.size === 0;

  const viewApplicable = useMemo(() => {
    if (!isPrestaging) return realApplicable;
    const set = new Set<string>();
    for (const m of meta) set.add(m.name);
    return set;
  }, [isPrestaging, realApplicable, meta]);

  const inActive = useMemo(() => {
    const top = (dash as { composite_fitness_formula?: string | null } | null)?.composite_fitness_formula;
    if (top) return whatifIdentifiersInFormula(top);
    for (const c of inflightCandidates) {
      const f = (c.stats as { composite_fitness_formula?: string | null } | undefined)?.composite_fitness_formula;
      if (f) return whatifIdentifiersInFormula(f);
    }
    const evalKeys = new Set<string>();
    for (const c of inflightCandidates) {
      for (const k of Object.keys(c.stats?.evaluators ?? {})) evalKeys.add(k);
    }
    return evalKeys;
  }, [dash, inflightCandidates]);

  const rows = useMemo(() => {
    const built = isPrestaging
      ? meta.map<Row>((m) => ({
          displayName: m.name,
          registryName: m.name,
          applicable: true,
          description: m.description,
          direction: m.direction,
        }))
      : buildRows(meta, realApplicable);
    const bucketOf = (r: Row) => {
      if (!r.applicable) return 3;
      if (inActive.has(r.displayName)) return 0;
      if (selected.has(r.displayName)) return 1;
      return 2;
    };
    return built.slice().sort((a, b) => bucketOf(a) - bucketOf(b));
  }, [meta, realApplicable, inActive, selected, isPrestaging]);

  useEffect(() => {
    if (seeded) {
      const drop = [...selected].filter((n) => !viewApplicable.has(n));
      if (drop.length) {
        const next = new Set(selected);
        for (const n of drop) next.delete(n);
        setFitnessState({ selected: next });
      }
      return;
    }
    if (viewApplicable.size === 0) return;
    const seed = new Set<string>();
    for (const r of rows) {
      if (r.applicable && inActive.has(r.displayName)) seed.add(r.displayName);
    }
    setFitnessState({ selected: seed, seeded: true });
  }, [rows, inActive, viewApplicable, seeded, selected]);

  const toggle = (name: string) => {
    const next = new Set(selected);
    if (next.has(name)) next.delete(name);
    else next.add(name);
    setSelected(next);
  };

  const resetActual = () => {
    const next = new Set<string>();
    for (const a of viewApplicable) {
      if (inActive.has(a)) next.add(a);
    }
    setSelected(next);
  };
  const selectNone = () => setSelected(new Set());
  const selectAll = () => {
    const next = new Set<string>();
    for (const a of viewApplicable) next.add(a);
    setSelected(next);
  };

  const selectableCount = rows.filter((r) => r.applicable).length;

  // ── 4. Assemble the unified bar list:
  //   [origin] + [historical round candidates...] + [in-flight current round]
  const bars: BarSlot[] = useMemo(() => {
    const out: BarSlot[] = [];

    // Origin — take from any round file (they all carry origin_accuracy)
    // or fall back to the dash field. No evaluator breakdown for origin.
    let originAccuracy: number | null = null;
    if (typeof dash?.origin_accuracy === "number") {
      originAccuracy = dash.origin_accuracy;
    } else {
      const firstWithOrigin = history.find((h) => typeof h.origin_accuracy === "number");
      if (firstWithOrigin) originAccuracy = firstWithOrigin.origin_accuracy ?? null;
    }
    if (originAccuracy != null || history.length > 0 || inflightCandidates.length > 0) {
      out.push({
        key: "C0",
        label: "C0",
        accuracy: originAccuracy,
        composite: null,
        whatif: null,
        started: originAccuracy != null,
      });
    }

    const historicalRoundsSeen = new Set<number>();
    for (const h of history) {
      const roundNum = Number(h.round ?? 0);
      historicalRoundsSeen.add(roundNum);
      const cands = h.candidate_scores ?? [];
      cands.forEach((c, i) => {
        const ev = c.evaluators ?? {};
        const acc = typeof c.accuracy === "number" ? c.accuracy : null;
        const comp = typeof c.composite_fitness === "number" ? c.composite_fitness : null;
        out.push({
          key: `R${roundNum}.${i}`,
          label: c.label || candidateLabel(roundNum, i),
          accuracy: acc,
          composite: comp,
          whatif: correctedFromEvaluators(ev, selected, rows),
          started: acc != null || comp != null || Object.keys(ev).length > 0,
        });
      });
    }

    // In-flight: only if the current round hasn't yet been written to disk.
    if (
      inflightCandidates.length > 0 &&
      !historicalRoundsSeen.has(currentRound)
    ) {
      for (const c of inflightCandidates) {
        const i = Number(c.idx);
        if (!Number.isFinite(i) || i < 0) continue;
        const ev = c.stats?.evaluators ?? {};
        const acc = typeof c.stats?.accuracy === "number"
          ? c.stats.accuracy
          : runningFromSamples(c.samples);
        const comp = typeof c.stats?.composite_fitness === "number"
          ? c.stats.composite_fitness
          : null;
        out.push({
          key: `live.${currentRound}.${i}`,
          label: c.label || candidateLabel(currentRound, i),
          accuracy: acc,
          composite: comp,
          whatif: correctedFromEvaluators(ev, selected, rows),
          started:
            (c.samples?.length ?? 0) > 0 ||
            typeof c.stats?.accuracy === "number" ||
            typeof c.stats?.composite_fitness === "number" ||
            Object.keys(ev).length > 0,
        });
      }
    }

    return out;
  }, [dash, history, inflightCandidates, selected, rows, currentRound]);

  // Rank summary (only rendered when What-If is open) — now spans every bar
  // including origin and historical rounds.
  let summary: React.ReactNode;
  if (bars.length === 0) {
    summary = (
      <span className="empty">
        Evaluator registry loads with round 1, then candidates surface here as scoring completes — toggle these on/off to preview alternative scoring without re-running.
      </span>
    );
  } else if (selected.size === 0) {
    summary = <span className="empty">No evaluators selected — pick one or more tiles above to recompute scores.</span>;
  } else {
    const lines = bars.map((b) => ({
      key: b.key,
      label: b.label,
      actual: b.composite ?? b.accuracy,
      whatif: b.whatif,
    }));
    const rankActual = ranks(lines.map((l) => ({ key: l.key, v: l.actual })));
    const rankWhatif = ranks(lines.map((l) => ({ key: l.key, v: l.whatif })));
    const wA = pickWinner(lines.map((l) => ({ key: l.key, v: l.actual })));
    const wW = pickWinner(lines.map((l) => ({ key: l.key, v: l.whatif })));
    const winnerLabel = (k: string | null) =>
      k == null ? "—" : (lines.find((l) => l.key === k)?.label ?? "—");
    let movedUp = 0, movedDown = 0, flat = 0;
    for (const l of lines) {
      const rA = rankActual.get(l.key);
      const rW = rankWhatif.get(l.key);
      if (rA == null || rW == null) continue;
      if (rA > rW) movedUp += 1;
      else if (rA < rW) movedDown += 1;
      else flat += 1;
    }
    const winnerSwap = wA != null && wW != null && wA !== wW;
    const fmt = (v: number | null) => (v == null ? "—" : v.toFixed(3));
    summary = (
      <>
        <div>
          {winnerSwap
            ? <span className="rank-up">winner flips {winnerLabel(wA)} → {winnerLabel(wW)}</span>
            : <span className="rank-flat">winner unchanged ({winnerLabel(wA)})</span>}
        </div>
        <div>
          <span className="rank-up">▲ {movedUp}</span> moved up · <span className="rank-down">▼ {movedDown}</span> moved down · <span className="rank-flat">· {flat}</span> unchanged
        </div>
        <div style={{ marginTop: 6 }}>
          candidates: {lines.map((l, i) => {
            const rA = rankActual.get(l.key);
            const rW = rankWhatif.get(l.key);
            const arrow = rA != null && rW != null
              ? (rA > rW ? <span className="rank-up">▲</span> : rA < rW ? <span className="rank-down">▼</span> : <span className="rank-flat">·</span>)
              : <span className="rank-flat">—</span>;
            return (
              <span key={l.key}>
                {i > 0 && " · "}
                {l.label} {fmt(l.actual)}→{fmt(l.whatif)} {arrow}
              </span>
            );
          })}
        </div>
      </>
    );
  }

  return (
    <div className={`card fitness-card${showWhatIf ? " whatif-open" : ""}`}>
      <div className="card-title">
        <span style={{ display: "inline-flex", alignItems: "center", gap: 8 }}>
          <span>Per-candidate fitness</span>
          <span className="badge" title="Bars span every round, origin first; chip = current optimization round">R{currentRound}</span>
        </span>
        <div className="fitness-toggles" role="group" aria-label="Score views">
          <button
            type="button"
            className={`fitness-chip${showComposite ? " on" : ""}`}
            aria-pressed={showComposite}
            onClick={() => setShowComposite((v) => !v)}
            title="Pair the formula-weighted composite score alongside the accuracy bar."
          >
            Composite
          </button>
          <button
            type="button"
            className={`fitness-chip${showWhatIf ? " on" : ""}`}
            aria-pressed={showWhatIf}
            onClick={() => setShowWhatIf((v) => !v)}
            title="Open the what-if ablation: pick evaluators to recompute scores client-side."
          >
            What-If
          </button>
        </div>
      </div>
      {(showComposite || showWhatIf) && (
        <div className="fitness-legend">
          <span><span className="dot accuracy" />accuracy</span>
          {showComposite && <span><span className="dot composite" />composite</span>}
          {showWhatIf && <span><span className="dot whatif" />what-if</span>}
        </div>
      )}
      <FitnessChart
        bars={bars}
        showComposite={showComposite}
        showWhatIf={showWhatIf}
        themeKey={themeKey}
      />
      {showWhatIf && (
        <div className="fitness-whatif">
          <div className="whatif-intro">
            Toggle evaluators on/off to recompute candidate scores as <code>mean(direction-corrected selected)</code> and watch the candidate ranking shift. The actual <code>composite_fitness</code> on disk is unchanged — this is a client-side preview to explore <em>&quot;what if I scored without X?&quot;</em>
          </div>
          <div className="whatif-legend">
            <span><span className="swatch checked">✓</span>selected (counts in what-if)</span>
            <span><span className="swatch active" />used in actual formula</span>
            <span><span className="swatch optional" />available, not in formula</span>
            <span><span className="swatch disabled" />not applicable to this pipeline</span>
          </div>
          <div className="whatif-controls">
            <span className="whatif-status">{selected.size} of {selectableCount} evaluator{selectableCount === 1 ? "" : "s"} selected</span>
            <div className="whatif-actions">
              <button type="button" className="whatif-btn" onClick={resetActual}>Reset to actual</button>
              <button type="button" className="whatif-btn" onClick={selectNone}>None</button>
              <button type="button" className="whatif-btn" onClick={selectAll}>All</button>
            </div>
          </div>
          <div className="whatif-grid-wrap">
            <div className="whatif-grid">
              {rows.map((r, idx) => {
                const enabled = selected.has(r.displayName);
                const inAct = inActive.has(r.displayName);
                const cls = ["whatif-sq"];
                if (enabled) cls.push("on");
                if (inAct) cls.push("in-active");
                if (!r.applicable) cls.push("disabled");
                const dirClass = r.direction === "low" ? "down" : "up";
                const dirGlyph = r.direction === "low" ? "↓" : "↑";
                const dirTip = r.direction === "low" ? TERMS.whatif_down : TERMS.whatif_up;
                return (
                  <button
                    key={`${r.registryName}__${r.displayName}__${idx}`}
                    type="button"
                    className={cls.join(" ")}
                    disabled={!r.applicable}
                    role="checkbox"
                    aria-checked={enabled}
                    aria-disabled={!r.applicable}
                    aria-label={r.displayName}
                    tabIndex={r.applicable ? 0 : -1}
                    title={r.description || r.displayName}
                    onClick={() => r.applicable && toggle(r.displayName)}
                  >
                    <span className="whatif-tick" aria-hidden="true">
                      <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="3.2" strokeLinecap="round" strokeLinejoin="round">
                        <path d="M2.5 8.5 L6.5 12.5 L13.5 3.5" />
                      </svg>
                    </span>
                    <span className={`whatif-dir ${dirClass}`} title={dirTip} aria-hidden="true">{dirGlyph}</span>
                    <span className="whatif-ico">
                      {whatifIconFor(r.displayName, r.registryName)}
                    </span>
                    <span className="whatif-name">{r.displayName}</span>
                  </button>
                );
              })}
              {rows.length === 0 && (
                <div className="fitness-empty" style={{ gridColumn: "1 / -1" }}>
                  Evaluator registry loads once the optimizer publishes round 1.
                </div>
              )}
            </div>
          </div>
          <div className="whatif-summary">{summary}</div>
        </div>
      )}
    </div>
  );
}
