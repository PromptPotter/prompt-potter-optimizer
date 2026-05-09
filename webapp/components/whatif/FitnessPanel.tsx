"use client";
import { useEffect, useMemo } from "react";
import { TERMS } from "@/lib/terms";
import { WHATIF_INLINE_META, buildRows, whatifIdentifiersInFormula, type Row } from "./meta";
import { whatifIconFor } from "./icons";
import { FitnessChart } from "./FitnessChart";
import { setFitnessState, useFitnessState } from "./fitness-store";
import { parseSampleLine } from "@/lib/sample-line";
import type { DashboardSnapshot } from "@/lib/poll";

interface Candidate {
  idx?: number;
  stats?: {
    composite_fitness?: number;
    accuracy?: number;
    evaluators?: Record<string, number>;
  };
  // Live HIT/MISS lines from LiveDashboardProjection. Populated per-sample
  // before stats lands at candidate-eval completion.
  samples?: string[];
}

interface Props {
  dash: DashboardSnapshot | null;
  themeKey: string;
}

function correctedScore(cand: Candidate, selected: Set<string>, rows: Row[]): number | null {
  const ev = cand.stats?.evaluators ?? {};
  let sum = 0;
  let n = 0;
  for (const sel of selected) {
    const v = ev[sel];
    if (v == null) continue;
    const r = rows.find((rr) => rr.displayName === sel);
    const direction = r?.direction ?? "high";
    sum += direction === "low" ? 1 - v : v;
    n += 1;
  }
  return n > 0 ? sum / n : null;
}

function ranks(lines: { idx: number; v: number | null }[]): Map<number, number> {
  const sortable = lines.filter((l) => l.v != null).slice().sort((a, b) => (b.v as number) - (a.v as number));
  const m = new Map<number, number>();
  sortable.forEach((l, i) => m.set(l.idx, i + 1));
  return m;
}

function pickWinner(lines: { idx: number; v: number | null }[]): number | null {
  let best: number | null = null;
  let bestVal = -Infinity;
  for (const l of lines) {
    if (l.v == null) continue;
    if (l.v > bestVal) {
      bestVal = l.v;
      best = l.idx;
    }
  }
  return best;
}

export function FitnessPanel({ dash, themeKey }: Props) {
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

  const cr = (dash?.current_round?.nodes as Record<string, { output?: { candidates?: Candidate[] } }> | undefined)?.l1_score ?? null;
  const candidates: Candidate[] = cr?.output?.candidates ?? [];

  const realApplicable = useMemo(() => {
    const set = new Set<string>();
    for (const c of candidates) {
      const ev = c.stats?.evaluators ?? {};
      for (const k of Object.keys(ev)) set.add(k);
    }
    return set;
  }, [candidates]);

  // Pre-staging: when no candidate has evaluator data yet (baseline /
  // pre-round-1), short-circuit buildRows and emit one row per registry
  // entry with applicable=true. Going through buildRows with bare registry
  // names as `applicable` triggers its `endsWith("_" + m.name)` suffix-match
  // logic and produces duplicates (e.g. mean_retrieval_shortfall matches
  // *_retrieval_shortfall too).
  const isPrestaging = realApplicable.size === 0;

  // `viewApplicable` is the operator-visible selectable set. In pre-staging
  // (no candidate scores on disk yet) it falls back to the inline meta
  // registry — same set `rows` marks applicable=true above — so seeding
  // and "Reset to actual" can do useful work before round 1 lands.
  const viewApplicable = useMemo(() => {
    if (!isPrestaging) return realApplicable;
    const set = new Set<string>();
    for (const m of meta) set.add(m.name);
    return set;
  }, [isPrestaging, realApplicable, meta]);

  // Resolve the "in actual formula" highlight set. Priority:
  //   1. Top-level dash.composite_fitness_formula — preferred when populated
  //   2. Per-candidate stats.composite_fitness_formula — sometimes lands first
  //   3. Union of every candidate's stats.evaluators keys — these are the
  //      evaluators being computed this round to feed the active formula
  //      (broader than literal tokens, but a faithful proxy when the formula
  //      string itself isn't on disk yet — which is common during baseline /
  //      round 0 where the projection hasn't propagated it).
  const inActive = useMemo(() => {
    const top = (dash as { composite_fitness_formula?: string | null } | null)?.composite_fitness_formula;
    if (top) return whatifIdentifiersInFormula(top);
    for (const c of candidates) {
      const f = (c.stats as { composite_fitness_formula?: string | null } | undefined)?.composite_fitness_formula;
      if (f) return whatifIdentifiersInFormula(f);
    }
    const evalKeys = new Set<string>();
    for (const c of candidates) {
      const ev = c.stats?.evaluators ?? {};
      for (const k of Object.keys(ev)) evalKeys.add(k);
    }
    return evalKeys;
  }, [dash, candidates]);

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

  // Initialize selection to mirror the active formula. Seed against
  // `viewApplicable` so pre-staging (no candidates yet) still pre-checks the
  // formula's evaluators against the meta-registry rows.
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
  const nVariants = Math.max(
    Number((dash as { n_variants?: number } | null)?.n_variants) || 0,
    ...candidates.map((c) => Number(c.idx ?? -1) + 1),
    candidates.length,
  );

  // Rank summary (only rendered when What-If is open)
  let summary: React.ReactNode;
  if (candidates.length === 0) {
    summary = (
      <span className="empty">
        Evaluator registry loads with round 1, then candidates surface here as scoring completes — toggle these on/off to preview alternative scoring without re-running.
      </span>
    );
  } else if (selected.size === 0) {
    summary = <span className="empty">No evaluators selected — pick one or more tiles above to recompute scores.</span>;
  } else {
    const liveActual = (c: Candidate): number | null => {
      const samples = c.samples ?? [];
      if (samples.length === 0) return null;
      let hits = 0;
      let total = 0;
      for (const raw of samples) {
        const p = parseSampleLine(raw);
        if (p.status === "HIT") { hits += 1; total += 1; }
        else if (p.status === "MISS") { total += 1; }
      }
      return total > 0 ? hits / total : null;
    };
    const lines = candidates.map((c) => ({
      idx: c.idx ?? -1,
      actual: typeof c.stats?.composite_fitness === "number"
        ? c.stats.composite_fitness
        : liveActual(c),
      whatif: correctedScore(c, selected, rows),
    }));
    const rankActual = ranks(lines.map((l) => ({ idx: l.idx, v: l.actual })));
    const rankWhatif = ranks(lines.map((l) => ({ idx: l.idx, v: l.whatif })));
    const wA = pickWinner(lines.map((l) => ({ idx: l.idx, v: l.actual })));
    const wW = pickWinner(lines.map((l) => ({ idx: l.idx, v: l.whatif })));
    let movedUp = 0, movedDown = 0, flat = 0;
    for (const l of lines) {
      const rA = rankActual.get(l.idx);
      const rW = rankWhatif.get(l.idx);
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
            ? <span className="rank-up">winner flips C{wA} → C{wW}</span>
            : <span className="rank-flat">winner unchanged (C{wA ?? "—"})</span>}
        </div>
        <div>
          <span className="rank-up">▲ {movedUp}</span> moved up · <span className="rank-down">▼ {movedDown}</span> moved down · <span className="rank-flat">· {flat}</span> unchanged
        </div>
        <div style={{ marginTop: 6 }}>
          candidates: {lines.map((l, i) => {
            const rA = rankActual.get(l.idx);
            const rW = rankWhatif.get(l.idx);
            const arrow = rA != null && rW != null
              ? (rA > rW ? <span className="rank-up">▲</span> : rA < rW ? <span className="rank-down">▼</span> : <span className="rank-flat">·</span>)
              : <span className="rank-flat">—</span>;
            return (
              <span key={l.idx}>
                {i > 0 && " · "}
                C{l.idx} {fmt(l.actual)}→{fmt(l.whatif)} {arrow}
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
        <span>Per-candidate fitness</span>
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
        candidates={candidates}
        selected={selected}
        rows={rows}
        nVariants={nVariants}
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
