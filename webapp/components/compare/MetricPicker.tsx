"use client";
// WHICH number the Compare tab is about. The catalogue, its labels, its units and its prose are
// all SERVED — nothing here restates a metric the server owns, so a channel added in Python
// arrives with no edit on this side.
//
// `Menu` + `MenuRadioGroup` rather than `SegmentedControl` (whose contract is 2-4 options; there
// are seven plus a composed one) and rather than `Chip` (a non-exclusive toggle; this is a single
// choice). Never a hand-rolled dropdown.


import { CommitInput, Menu, MenuRadioGroup } from "@/components/ui";
import type { MetricReading } from "@/lib/api/types";
import { cx } from "@/lib/cx";

// The composed-metric spelling lives here and nowhere else: `ComparePane` holds one opaque
// `metric` string and `reads.ts` passes it through, so no second copy of this prefix exists.
//
// There is deliberately no `MEASURAND` constant beside it. The default metric is the SERVER's to
// name, and every read echoes back the spec it resolved, so the picker takes its label and its
// tick off `reading.spec` rather than mirroring a key that could drift.
const EXPR_PREFIX = "expr:";

export const isCustomMetric = (metric: string) => metric.startsWith(EXPR_PREFIX);
export const customMetric = (expression: string) => EXPR_PREFIX + expression;
export const expressionOf = (metric: string) => metric.slice(EXPR_PREFIX.length);

export function MetricPicker({
  reading,
  metric,
  onMetric,
}: {
  reading: MetricReading;
  metric: string;
  onMetric: (key: string) => void;
}) {
  const custom = isCustomMetric(metric);
  const label = custom ? "Custom" : reading.spec.label;
  // Picking "Custom" SEEDS from whatever is on screen, so the input opens on a formula that
  // already works rather than on an empty string the server would reject on sight.
  const customValue = custom ? metric : customMetric(reading.spec.expression);
  return (
    <Menu
      renderTrigger={({ open, toggle }) => (
        <button
          type="button"
          className="cmp-button"
          aria-haspopup="menu"
          aria-expanded={open}
          onClick={toggle}
        >
          {label} ▾
        </button>
      )}
    >
      {({ close }) => (
        <MenuRadioGroup
          label="Metric"
          // The RESOLVED key, not the local one: the local string is empty until the operator
          // picks, and the server's default is what is actually on screen.
          value={custom ? customValue : reading.spec.key}
          options={[
            ...reading.catalogue.map((m) => ({ value: m.key, label: m.label })),
            { value: customValue, label: "Custom expression…" },
          ]}
          onChange={(v) => {
            onMetric(v);
            close();
          }}
        />
      )}
    </Menu>
  );
}

// The composed metric's input. It COMMITS on Enter or blur, never per keystroke: the metric is a
// fetch key, so a keystroke-driven one fires a request per character, 400s on every half-typed
// formula, and — because `useFetch` resets in the render phase — blanks the card under the cursor
// that is still typing.
export function MetricExpression({
  reading,
  metric,
  invalid,
  onMetric,
}: {
  reading: MetricReading;
  metric: string;
  invalid: string | null;
  onMetric: (metric: string) => void;
}) {
  const expression = expressionOf(metric);
  return (
    <div className="cmp-expr">
      <label className="cmp-expr-label" htmlFor="cmp-expr-input">
        Compose a metric
      </label>
      <CommitInput
        id="cmp-expr-input"
        className={cx("cmp-expr-input", invalid && "cmp-expr-bad")}
        value={expression}
        placeholder="lift / latency"
        aria-invalid={invalid ? true : undefined}
        aria-describedby="cmp-expr-names"
        // A blank commit is not a metric — it clears the field, and the picker above is how the
        // selection goes back to a catalogue key.
        onCommit={(v: string) => {
          if (v.trim()) onMetric(customMetric(v.trim()));
        }}
      />
      <p className="l4-subtle" id="cmp-expr-names">
        Enter to apply. Available: {reading.namespace.join(", ")}. Units and direction are yours to
        know — a composed metric has none the server can name.
      </p>
      {invalid ? <p className="l4-warn">{invalid}</p> : null}
    </div>
  );
}
