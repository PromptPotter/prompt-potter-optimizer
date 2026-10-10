"use client";

import type { SubjectReading } from "@/lib/api";
import { SUBJECT_KIND_LABELS } from "@/lib/api/types.generated";
import type { CladogramChannel } from "@/components/candidates/Forest";
import { useCompareSelection } from "@/lib/compare-selection";
import type { CompareItem, RunGroup } from "@/lib/derivations";
import { cx } from "@/lib/cx";
import { ChannelColumn, type ColumnPlace } from "./ChannelColumn";
import { ChannelDetail } from "./ChannelDetail";
import { CostFacts, HeadlineBadges, Metric, Swatch, benchLead } from "./channel-parts";
import { useChannelModel, type ChannelModel, type MetricUnit } from "./useChannelModel";

export function ChannelCard({
  item,
  ownNote,
  points,
  spec,
  column,
  run,
}: {
  item: CompareItem;
  ownNote: boolean;
  points: readonly CladogramChannel[];
  spec: { unit: MetricUnit; axis_label: string };
  column: ColumnPlace | null;
  run: RunGroup | null;
}) {
  const { reading, headline, slot } = item;
  const model = useChannelModel(item, run, spec.unit, column ? "winner" : "own");
  // Null for an unread channel: it plots no series, so any ink here would be another channel's.
  const own = slot === null ? null : (points[slot] ?? null);
  const rulerNote = ownNote && reading?.comparable === false;
  const notes = <ChannelNotes reading={reading} withdrawn={model.withdrawn} rulerNote={rulerNote && !headline} />;
  const detail = (
    <ChannelDetail
      model={model}
      item={item}
      rulerNote={rulerNote}
      points={points}
      own={own}
      axis={spec.axis_label}
      inColumn={column !== null}
    />
  );

  if (column && headline) {
    return (
      <ChannelColumn model={model} headline={headline} run={run} ink={own?.ink ?? null} place={column}>
        {notes}
        {detail}
      </ChannelColumn>
    );
  }
  return (
    <li className="cmp-channel">
      <ChannelIdRow model={model} item={item} ink={own?.ink ?? null} axis={spec.axis_label} />
      {notes}
      <details className="cmp-channel-detail" open={model.folds.detail}>
        <summary
          onClick={(e) => {
            e.preventDefault();
            model.toggleFold("detail");
          }}
        >
          {reading === null ? "Lineage" : "Search reading, lineage and configuration"}
        </summary>
        {detail}
      </details>
    </li>
  );
}

function ChannelIdRow({
  model,
  item: { reading, headline },
  ink,
  axis,
}: {
  model: ChannelModel;
  item: CompareItem;
  ink: string | null;
  axis: string;
}) {
  const { remove } = useCompareSelection();
  const { subject, level, withdrawn } = model;
  const lead = benchLead(headline);
  return (
    <div className="cmp-channel-row">
      <div className="cmp-channel-id">
        <span className="cmp-channel-name">
          <Swatch ink={ink} />
          {headline
            ? headline.optimizer
            : reading && reading.kind !== "campaign"
              ? reading.label
              : model.campaignName}
        </span>
        <span className="cmp-channel-sub" title={subject}>
          {reading ? `${SUBJECT_KIND_LABELS[reading.kind]} · ${reading.campaign_id}` : "nothing measured"}
        </span>
        {headline && <HeadlineBadges row={headline} />}
      </div>
      {headline ? (
        <>
          <Metric label="bench" {...lead.score} />
          <Metric label="lift over origin" {...lead.lift} />
        </>
      ) : (
        level && (
          <div className={cx("cmp-channel-lead", withdrawn && "cmp-channel-unknown")}>
            <Metric label={axis} value={level.value} band={level.band} />
          </div>
        )
      )}
      <button
        type="button"
        className="cmp-link cmp-channel-close"
        aria-label="Remove this channel from the comparison"
        onClick={() => remove(subject)}
      >
        ✕
      </button>
      {headline && <CostFacts row={headline} />}
    </div>
  );
}

function ChannelNotes({
  reading,
  withdrawn,
  rulerNote,
}: {
  reading: SubjectReading | null;
  withdrawn: boolean;
  rulerNote: boolean;
}) {
  if (reading === null) {
    return (
      <p className="note-info">
        Nothing measured at this address — it is named in the read&rsquo;s{" "}
        <code>unread_subjects</code> rather than counted as a low number. Open the lineage below
        and pick a point that has run.
      </p>
    );
  }
  return (
    <>
      {withdrawn && (
        <p className="note-warn">
          ✗ A setting was changed at or above the point this channel reads, and nothing ran
          under it. Every number here is unknown until it is measured — restore it below, or
          steer &amp; fork from that searchpoint to actually run it.
        </p>
      )}
      {rulerNote && <p className="note-warn">{reading.comparable_note}</p>}
      {reading.human_intervened && (
        <p className="note-warn">
          An operator intervened mid-run on this cycle, so it is no longer purely reproducible.
        </p>
      )}
    </>
  );
}
