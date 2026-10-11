"use client";

import type { ReactNode } from "react";
import { createPortal } from "react-dom";
import type { ArmNode, HeadToHeadRow } from "@/lib/api";
import { campaignCard, courseStats, type RunGroup } from "@/lib/derivations";
import { useCompareSelection } from "@/lib/compare-selection";
import { cx } from "@/lib/cx";
import { fmtDuration, fmtSigned, shortId } from "@/lib/format";
import { SummaryBlock } from "@/components/shell/SummaryBlock";
import { SegmentedControl, Term } from "@/components/ui";
import {
  HeadlineBadges,
  MainLine,
  Metric,
  SpentReading,
  Swatch,
  benchLead,
} from "./channel-parts";
import type { ChannelModel } from "./useChannelModel";

export const COLUMN_ROWS = [
  "head",
  "optimizer",
  "bench",
  "lift",
  "per_usd",
  "spent",
  "worked",
  "standing",
  "reads",
  "line",
  "more",
] as const;
export type ColumnRow = (typeof COLUMN_ROWS)[number];

export const ROW_LABEL: Record<ColumnRow, ReactNode> = {
  head: null,
  optimizer: "optimizer",
  bench: "bench",
  lift: "lift over origin",
  per_usd: "lift / USD",
  spent: "spent",
  worked: "worked",
  standing: "standing",
  reads: "reads at",
  line: (
    <Term
      content={
        <p className="cmp-line-key">
          Each round&rsquo;s pick, from the origin up to the searchpoint above: its accuracy on the
          cells it measured, then its lift over its parent on those same cells, toned by the 95%
          interval. The lift is the round&rsquo;s verdict — the accuracy moves with the subset each
          round drew. A held round crowned nobody, so the line kept its parent.
        </p>
      }
    >
      main line
    </Term>
  ),
  more: null,
};

export interface ColumnPlace {
  rows: readonly ColumnRow[];
  panelHost: HTMLElement | null;
  order: number;
}

export function ChannelColumn({
  model,
  headline,
  run,
  ink,
  place,
  children,
}: {
  model: ChannelModel;
  headline: HeadToHeadRow;
  run: RunGroup | null;
  ink: string | null;
  place: ColumnPlace;
  children: ReactNode;
}) {
  const { remove } = useCompareSelection();
  const { campaignName, head, selected, setSelected, folds, line } = model;
  const lead = benchLead(headline);
  const summary = run && campaignCard(run, model.tree.root);
  const close = (
    <button
      type="button"
      className="cmp-link cmp-channel-close"
      aria-label="Remove this campaign from the comparison"
      onClick={() => remove(model.subject)}
    >
      ✕
    </button>
  );
  const cell = (row: ColumnRow): ReactNode => {
    switch (row) {
      case "head":
        return (
          <div className="cmp-col-head">
            <Swatch ink={ink} />
            {summary ? (
              <SummaryBlock
                dense
                facts={{
                  ...summary,
                  tags: [shortId(headline.campaign_id), ...(summary.tags ?? [])],
                  stats: courseStats(run, model.tree.root),
                }}
                actions={close}
              />
            ) : (
              <span className="cmp-channel-name">
                {campaignName}
                {close}
              </span>
            )}
          </div>
        );
      case "optimizer":
        return <span className="cmp-col-optimizer">{headline.optimizer}</span>;
      case "bench":
        return <Metric {...lead.score} />;
      case "lift":
        return <Metric {...lead.lift} />;
      case "per_usd":
        return fmtSigned(headline.bench.cost.lift_per_usd, 2);
      case "spent":
        return <SpentReading metered={headline.spend_metered} />;
      case "worked":
        return headline.worked_s === null ? "—" : fmtDuration(headline.worked_s);
      case "standing":
        return <HeadlineBadges row={headline} />;
      case "reads":
        return <ColumnReads head={head} selected={selected} setSelected={setSelected} />;
      case "line":
        return <MainLine steps={line} />;
      case "more":
        return (
          <button
            type="button"
            className="cmp-link"
            aria-expanded={folds.detail}
            onClick={() => model.toggleFold("detail")}
          >
            {folds.detail ? "▾" : "▸"} Reading, lineage, config
          </button>
        );
    }
  };
  return (
    <>
      <div className="cmp-col" role="group" aria-label={`${headline.optimizer} · ${campaignName}`}>
        {place.rows.map((r) => (
          <div key={r} className={cx("cmp-cell", `is-${r}`)}>
            {cell(r)}
          </div>
        ))}
      </div>
      {folds.detail &&
        place.panelHost &&
        createPortal(
          <section
            className="cmp-channel"
            style={{ order: place.order }}
            aria-label={`${headline.optimizer} · ${campaignName} in detail`}
          >
            <span className="cmp-channel-name">
              <Swatch ink={ink} />
              {headline.optimizer} · {campaignName}
            </span>
            {children}
          </section>,
          place.panelHost,
        )}
    </>
  );
}

function ColumnReads({
  head,
  selected,
  setSelected,
}: {
  head: ChannelModel["head"];
  selected: ArmNode | null;
  setSelected: (next: ArmNode | null) => void;
}) {
  return (
    <div className="cmp-col-reads">
      {head.options.length > 1 && (
        <SegmentedControl
          options={head.options}
          value={head.value}
          onChange={(v) => setSelected(head.nodeFor(v))}
          ariaLabel="Which searchpoint this column reads"
        />
      )}
      <span className="cmp-metric-band">
        {selected ? `${selected.label} · round ${selected.reading.arm.round}` : "—"}
      </span>
    </div>
  );
}
