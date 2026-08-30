"use client";
// The ONE scoring-mask form. It renders on the dashboard's candidates card and on a Compare
// channel, so it is chrome (`webapp/CLAUDE.md` § Component conventions) rather than either
// surface's.
//
// It replaced two editors for one idea: a slider grid that could only build a weighted sum, and a
// free-text field that could only take one. Each was unreachable from the other surface, so an
// operator who built a mask on the dashboard retyped it to compare it. Both forms survive here as
// MODES of one value (`scoring-mask.ts::ScoringMask`) — the grid is the readable way to say the
// common thing, the expression is the escape hatch for what a grid cannot spell, and switching is
// a fact about the value rather than about which tab you are on.
//
// Text commits on Enter or blur, never per keystroke: on Compare the mask is part of the fetch key,
// so a keystroke commit fires a request per character and 400s on every half-typed formula. The
// grid commits per click, which is the same rule — a toggle is not a half-value.

import type { ReactNode } from "react";
import { CommitInput, SegmentedControl } from "@/components/ui";
import { cx } from "@/lib/cx";
import { TERMS } from "@/lib/terms";
import { maskIconFor } from "@/components/candidates/icons";
import { DEFAULT_MASK_WEIGHT, type Row, type ScoringMask } from "./scoring-mask";

const MODES = [
  {
    value: "weights" as const,
    label: "Weights",
    title: "Build the criterion by picking evaluators and weighting them",
  },
  {
    value: "expression" as const,
    label: "Expression",
    title: "Type the criterion yourself — anything the grid cannot spell",
  },
];

export function ScoringMaskEditor({
  rows,
  inActive,
  mask,
  onMask,
  seeded,
  samples,
  onSamples,
  invalid,
  summary,
}: {
  // Evaluator tiles in display order. The dashboard narrows these to what this cycle MEASURED;
  // Compare has no such cycle and offers the whole served registry.
  rows: readonly Row[];
  // The evaluators the REALIZED composite names — the tile's "used in actual formula" state.
  inActive: ReadonlySet<string>;
  mask: ScoringMask;
  onMask: (mask: ScoringMask) => void;
  // WHERE the weights started, which is the difference between reading them as the criterion in
  // force and reading them as a starting point. `realized` = seeded from the served decomposition.
  // `default` = the active formula is not a weighted sum, so no coefficient exists to seed from.
  // `none` = there is no single active formula at all, which is every multi-campaign board.
  seeded: "realized" | "default" | "none";
  // The sample subset, as the operator types it. Omitted where the surface already owns a richer
  // editor of that same axis (the dashboard's chip strip) — two writers on one fact is what this
  // module exists to avoid.
  samples?: string;
  onSamples?: (raw: string) => void;
  invalid?: string | null;
  summary?: ReactNode;
}) {
  return (
    <div className="fitness-mask">
      <div className="mask-head">
        <SegmentedControl
          options={MODES}
          value={mask.kind}
          onChange={(kind) =>
            onMask(
              kind === "weights"
                ? { kind: "weights", selected: new Set(), weights: {} }
                : { kind: "expression", lens: "" },
            )
          }
          ariaLabel="How to build the scoring mask"
        />
        <span className="l4-subtle">
          Read this branch under a criterion it was not scored on. Every value left is one the run
          recorded — the elections are re-decided, never re-run.
        </span>
      </div>

      {mask.kind === "weights" ? (
        <WeightGrid rows={rows} inActive={inActive} mask={mask} onMask={onMask} seeded={seeded} />
      ) : (
        <label className="mask-field">
          <span className="cmp-expr-label">Score it as…</span>
          <CommitInput
            className={cx("cmp-expr-input", invalid && "cmp-expr-bad")}
            value={mask.lens}
            placeholder="score:accuracy - 0.05 * latency"
            aria-invalid={invalid ? true : undefined}
            onCommit={(lens) => onMask({ kind: "expression", lens })}
          />
        </label>
      )}

      {onSamples && (
        <label className="mask-field">
          <span className="cmp-expr-label">…over these samples only</span>
          <CommitInput
            className="cmp-expr-input"
            value={samples ?? ""}
            placeholder="3,7,11 — blank for every sample it measured"
            onCommit={onSamples}
          />
        </label>
      )}

      {invalid ? <p className="l4-warn">{invalid}</p> : null}
      {summary ? <div className="mask-summary">{summary}</div> : null}
    </div>
  );
}

function WeightGrid({
  rows,
  inActive,
  mask,
  onMask,
  seeded,
}: {
  rows: readonly Row[];
  inActive: ReadonlySet<string>;
  mask: Extract<ScoringMask, { kind: "weights" }>;
  onMask: (mask: ScoringMask) => void;
  seeded: "realized" | "default" | "none";
}) {
  const toggle = (name: string) => {
    const selected = new Set(mask.selected);
    if (!selected.delete(name)) selected.add(name);
    onMask({ ...mask, selected });
  };
  const setWeight = (name: string, weight: number) =>
    onMask({ ...mask, weights: { ...mask.weights, [name]: weight } });

  return (
    <>
      <div className="mask-legend">
        <span>
          <span className="swatch checked">✓</span>selected (counts in the mask)
        </span>
        <span>
          <span className="swatch active" />
          used in actual formula
        </span>
        <span>
          <span className="swatch optional" />
          available, not in formula
        </span>
      </div>
      {/* The weights are served, so "not seeded" is a fact about the active formula rather than a
          parse that fell short — and saying it is what keeps a default from reading as the
          criterion in force. */}
      {seeded === "default" && rows.length > 0 && (
        <p className="l4-subtle">
          The active formula is not a weighted sum, so these start from a default rather than from
          it. The lens they build is still applied to the record.
        </p>
      )}
      {seeded === "none" && rows.length > 0 && (
        <p className="l4-subtle">
          These channels can come from different campaigns, so there is no one active formula to
          start from. This builds a criterion from scratch and reads each channel under it.
        </p>
      )}
      <div className="mask-grid-wrap">
        <div className="mask-grid">
          {rows.map((r, idx) => {
            const enabled = mask.selected.has(r.displayName);
            const weight = mask.weights[r.displayName] ?? DEFAULT_MASK_WEIGHT;
            const down = r.direction === "low";
            return (
              <div
                key={`${r.registryName}__${r.displayName}__${idx}`}
                className={cx(
                  "mask-sq",
                  enabled && "on",
                  inActive.has(r.displayName) && "in-active",
                  !r.applicable && "disabled",
                )}
              >
                <button
                  type="button"
                  className="mask-sq-toggle"
                  disabled={!r.applicable}
                  role="checkbox"
                  aria-checked={enabled}
                  aria-disabled={!r.applicable}
                  aria-label={r.displayName}
                  tabIndex={r.applicable ? 0 : -1}
                  title={r.description || r.displayName}
                  onClick={() => r.applicable && toggle(r.displayName)}
                >
                  <span className="mask-tick" aria-hidden="true">
                    <svg
                      viewBox="0 0 16 16"
                      fill="none"
                      stroke="currentColor"
                      strokeWidth="3.2"
                      strokeLinecap="round"
                      strokeLinejoin="round"
                    >
                      <path d="M2.5 8.5 L6.5 12.5 L13.5 3.5" />
                    </svg>
                  </span>
                  <span
                    className={cx("mask-dir", down ? "down" : "up")}
                    title={down ? TERMS.mask_down : TERMS.mask_up}
                    aria-hidden="true"
                  >
                    {down ? "↓" : "↑"}
                  </span>
                  <span className="mask-ico">{maskIconFor(r.displayName, r.registryName)}</span>
                  <span className="mask-name">{r.displayName}</span>
                </button>
                {/* Weight thermometer — only where this evaluator counts. Seeded from the realized
                    composite coefficient, served. */}
                <div className="mask-weight" aria-hidden={!enabled || undefined}>
                  {enabled && r.applicable && (
                    <>
                      <input
                        type="range"
                        className="mask-weight-range"
                        min={0}
                        max={1}
                        step={0.05}
                        value={weight}
                        aria-label={`${r.displayName} weight`}
                        title={`Weight of ${r.displayName} in the masked score`}
                        onChange={(e) => setWeight(r.displayName, parseFloat(e.target.value))}
                      />
                      <span className="mask-weight-val">{weight.toFixed(2)}</span>
                    </>
                  )}
                </div>
              </div>
            );
          })}
          {rows.length === 0 && (
            <div className="fitness-empty" style={{ gridColumn: "1 / -1" }}>
              Evaluator registry loads once the optimizer publishes round 1.
            </div>
          )}
        </div>
      </div>
    </>
  );
}
