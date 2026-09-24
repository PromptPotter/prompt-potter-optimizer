"use client";
// The Compare metric picker. The catalogue, labels, units and prose are all SERVED. `Menu` +
// `MenuRadioGroup`: `SegmentedControl` caps at 4 options and `Chip` is a non-exclusive toggle.


import { CommitInput, Menu, MenuRadioGroup } from "@/components/ui";
import type { MetricReading } from "@/lib/api/types";
import { cx } from "@/lib/cx";

// The one spelling of the composed-metric prefix. No `MEASURAND` constant: the default is the
// server's, and the picker reads it back off `reading.spec`.
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
  // "Custom" SEEDS from what is on screen, so the input opens on a formula that already works.
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
          // The RESOLVED key: the local string is empty until the operator picks.
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

// COMMITS on Enter or blur, never per keystroke: the metric is a fetch key, so each keystroke would
// fire a request, 400 on a half-typed formula, and blank the card.
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
