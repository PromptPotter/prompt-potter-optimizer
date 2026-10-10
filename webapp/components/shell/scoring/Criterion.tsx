"use client";

import { useState, type ComponentType, type ReactNode } from "react";
import {
  CommitInput,
  IconBolt,
  IconCirclePlus,
  IconClose,
  IconCoin,
  IconDatabase,
  IconPulse,
  IconTarget,
  IconType,
  IconWarning,
  Menu,
  MenuItem,
  SegmentedControl,
  Term,
  ValueList,
} from "@/components/ui";
import type { CellTermMeta } from "@/lib/api/types.generated";
import { cx } from "@/lib/cx";
import { DIAL_TERMS, NO_DIALS, type ScoringMask } from "@/lib/scoring-mask";
import { TERMS } from "@/lib/terms";

const TERM_GLYPHS: Record<string, ComponentType> = {
  errored: IconWarning,
  degraded: IconPulse,
  latency: IconBolt,
  cached: IconDatabase,
  tokens: IconType,
  target_prompt_chars: IconType,
  cost: IconCoin,
};

const KINDS = [
  { value: "dials" as const, label: "Dials", title: "Weigh per-cell terms against correctness" },
  {
    value: "expression" as const,
    label: "Expression",
    title: "Type the criterion yourself — anything the dials cannot spell",
  },
];

export type CriterionRung = 0 | 1 | 2;

export interface CriterionMatcher {
  value: string;
  options?: readonly string[];
  onPick?: (matcher: string) => void;
}

const pct = (weight: number) => String(Math.round(weight * 1000) / 10);

function lineParts(mask: ScoringMask, matcher: CriterionMatcher | undefined): string[] {
  if (mask.kind === "expression") return [mask.lens.trim() || "no expression yet"];
  return [
    matcher?.value || "accuracy",
    ...DIAL_TERMS.flatMap((m) => {
      const w = mask.weights[m.name];
      return w !== undefined && w > 0 ? [`${m.name} ${pct(w)}%`] : [];
    }),
  ];
}

export function CriterionLine({
  mask,
  matcher,
}: {
  mask: ScoringMask;
  matcher?: CriterionMatcher;
}) {
  return (
    <span className="criterion-line">
      <span className="criterion-glyph" aria-hidden="true">
        <IconTarget />
      </span>
      {lineParts(mask, matcher).join(" · ")}
    </span>
  );
}

export function Criterion({
  mask,
  onMask,
  dialsOnly = false,
  matcher,
  anchors,
  formula,
  samples,
  onSamples,
  invalid,
  summary,
  note,
  startRung = 0,
  className,
}: {
  mask: ScoringMask;
  onMask?: (mask: ScoringMask) => void;
  dialsOnly?: boolean;
  matcher?: CriterionMatcher;
  // Served level per anchored dial; a term absent from it is not yet measured.
  anchors?: Readonly<Record<string, number>> | null;
  formula?: string | null;
  samples?: string;
  onSamples?: (raw: string) => void;
  invalid?: string | null;
  summary?: ReactNode;
  note?: ReactNode;
  startRung?: CriterionRung;
  className?: string;
}) {
  const [rung, setRung] = useState<CriterionRung>(startRung);
  // A dial at 0 has no other way to stay on screen.
  const [added, setAdded] = useState<readonly string[]>([]);

  const weights = mask.kind === "dials" ? mask.weights : NO_WEIGHTS;
  const shown = DIAL_TERMS.filter((m) => {
    const on = (weights[m.name] ?? 0) > 0;
    return onMask ? m.primary || on || added.includes(m.name) : on;
  });
  const addable = DIAL_TERMS.filter((m) => !shown.includes(m));

  const setWeight = (name: string, weight: number) =>
    onMask?.({ kind: "dials", weights: { ...weights, [name]: weight } });

  return (
    <div className={cx("criterion", className)}>
      <button
        type="button"
        className="criterion-head"
        aria-expanded={rung > 0}
        onClick={() => setRung(rung === 0 ? 1 : 0)}
      >
        <CriterionLine mask={mask} matcher={matcher} />
        <span className="criterion-caret" aria-hidden="true">
          {rung === 0 ? "▸" : "▾"}
        </span>
      </button>

      {rung > 0 && (
        <div className="criterion-body">
          {note ? <p className="l4-subtle">{note}</p> : null}

          <div className="criterion-rows">
            <div className="criterion-row">
              <span className="criterion-glyph" aria-hidden="true">
                <IconTarget />
              </span>
              <Term className="criterion-name" content={TERMS.criterion_accuracy}>
                accuracy
              </Term>
              {matcher?.onPick ? (
                <ValueList
                  name="Matcher"
                  values={[
                    matcher.value,
                    ...(matcher.options ?? []).filter((m) => m !== matcher.value),
                  ]}
                  onPick={matcher.onPick}
                />
              ) : (
                <span className="criterion-anchor">{matcher?.value ?? "the run's own matcher"}</span>
              )}
            </div>

            {mask.kind === "dials" ? (
              shown.map((term) => (
                <DialRow
                  key={term.name}
                  term={term}
                  weight={weights[term.name] ?? 0}
                  anchor={anchors?.[term.name]}
                  onWeight={onMask ? (w) => setWeight(term.name, w) : undefined}
                  onRemove={
                    onMask && !term.primary
                      ? () => {
                          setAdded(added.filter((name) => name !== term.name));
                          setWeight(term.name, 0);
                        }
                      : undefined
                  }
                />
              ))
            ) : onMask ? (
              <label className="criterion-field">
                <span className="cmp-expr-label">Score it as…</span>
                <CommitInput
                  className={cx("cmp-expr-input", invalid && "cmp-expr-bad")}
                  value={mask.lens}
                  placeholder="score:fitness * (1 - 0.1 * degraded)"
                  aria-invalid={invalid ? true : undefined}
                  onCommit={(lens) => onMask({ kind: "expression", lens })}
                />
              </label>
            ) : null}
          </div>

          {onMask && mask.kind === "dials" && addable.length > 0 && (
            <Menu
              align="left"
              renderTrigger={({ open, toggle }) => (
                <button
                  type="button"
                  className="criterion-add"
                  aria-expanded={open}
                  aria-label="Add a term to the criterion"
                  onClick={toggle}
                >
                  <IconCirclePlus />
                </button>
              )}
            >
              {({ close }) =>
                addable.map((term) => (
                  <MenuItem
                    key={term.name}
                    onClick={() => {
                      setAdded([...added, term.name]);
                      close();
                    }}
                  >
                    {term.name}
                    <span className="criterion-add-hint">{term.description}</span>
                  </MenuItem>
                ))
              }
            </Menu>
          )}

          {invalid ? <p className="note-warn">{invalid}</p> : null}
          {summary ? <div className="criterion-summary">{summary}</div> : null}

          {rung === 2 && (
            <div className="criterion-full">
              {onMask && !dialsOnly && (
                <SegmentedControl
                  options={KINDS}
                  value={mask.kind}
                  onChange={(kind) =>
                    onMask(kind === "dials" ? NO_DIALS : { kind: "expression", lens: "" })
                  }
                  ariaLabel="How to say the criterion"
                />
              )}
              {formula ? (
                <div className="criterion-field">
                  <span className="cmp-expr-label">Scored as</span>
                  <code className="criterion-formula">{formula}</code>
                </div>
              ) : null}
              {onSamples && (
                <label className="criterion-field">
                  <span className="cmp-expr-label">…over these samples only</span>
                  <CommitInput
                    className="cmp-expr-input"
                    value={samples ?? ""}
                    placeholder="3,7,11 — blank for every sample it measured"
                    onCommit={onSamples}
                  />
                </label>
              )}
            </div>
          )}

          {(formula || onSamples || (onMask && !dialsOnly)) && (
            <button
              type="button"
              className="cmp-link"
              aria-expanded={rung === 2}
              onClick={() => setRung(rung === 2 ? 1 : 2)}
            >
              {rung === 2 ? "Less" : "Formula and more"}
            </button>
          )}
        </div>
      )}
    </div>
  );
}

const NO_WEIGHTS: Readonly<Record<string, number>> = {};

function DialRow({
  term,
  weight,
  anchor,
  onWeight,
  onRemove,
}: {
  term: CellTermMeta;
  weight: number;
  anchor: number | undefined;
  onWeight?: (weight: number) => void;
  onRemove?: () => void;
}) {
  // Commits on release: on Compare the mask is in the fetch key, so a drag is one read.
  const [draft, setDraft] = useState(weight);
  const [seen, setSeen] = useState(weight);
  if (weight !== seen) {
    setSeen(weight);
    setDraft(weight);
  }
  const commit = () => {
    if (draft !== weight) onWeight?.(draft);
  };
  const Glyph = TERM_GLYPHS[term.name];

  return (
    <div className={cx("criterion-row", draft === 0 && "off")}>
      <span className="criterion-glyph" aria-hidden="true">
        {Glyph ? <Glyph /> : null}
      </span>
      <Term className="criterion-name" content={term.description || term.name}>
        {term.name}
      </Term>
      <input
        type="range"
        className="criterion-range"
        min={0}
        max={1}
        step={0.01}
        value={draft}
        disabled={!onWeight}
        aria-label={`${term.name} importance`}
        onChange={(e) => setDraft(parseFloat(e.target.value))}
        onPointerUp={commit}
        onKeyUp={commit}
        onBlur={commit}
      />
      {onWeight ? (
        <CommitInput
          className="criterion-pct"
          inputMode="decimal"
          value={pct(draft)}
          aria-label={`${term.name} importance, percent`}
          validate={(raw) => raw.trim() !== "" && Number(raw) >= 0 && Number(raw) <= 100}
          onCommit={(raw) => onWeight(Number(raw) / 100)}
        />
      ) : (
        <span className="criterion-pct">{pct(draft)}</span>
      )}
      <span className="criterion-unit">%</span>
      <span className="criterion-anchor">
        {term.dial !== "anchored"
          ? ""
          : anchor !== undefined
            ? `anchor ${Number(anchor.toPrecision(4))}`
            : "anchor measured at origin"}
      </span>
      {onRemove ? (
        <button
          type="button"
          className="criterion-remove"
          aria-label={`Remove ${term.name}`}
          onClick={onRemove}
        >
          <IconClose size={12} />
        </button>
      ) : (
        <span />
      )}
    </div>
  );
}
