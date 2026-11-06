import { type SubjectReading, SUBJECT_KIND_LABELS, type HierarchyCampaign } from "@/lib/api/types";
import type { CladogramChannel } from "@/components/candidates/Forest";
import { useChannelView, useCompareSelection } from "@/lib/state/compare-selection";
import type { CompareItem } from "@/lib/derivations";
import { cx } from "@/lib/cx";
import { Said } from "@/components/ui";
import { ChannelDetail } from "./ChannelDetail";
import { HeadlineBadges, HeadlineFacts, Metric, Swatch, benchLead } from "./channel-parts";
import type { MetricFormat } from "@/lib/format";
import {
  channelInk,
  channelLevel,
  rulerNoteOf,
  useChannelHead,
  useChannelTree,
  useInvalidated,
} from "./channel-hooks";

export interface ChannelProps {
  item: CompareItem;
  ownNote: boolean;
  points: readonly CladogramChannel[];
  spec: MetricFormat & { axis_label: string };
  run: HierarchyCampaign | null;
}

export function ChannelCard(props: ChannelProps) {
  const { item, ownNote, points, spec, run } = props;
  const { reading, headline } = item;
  const { index } = useChannelTree(run);
  const view = useChannelView(item, index);
  const head = useChannelHead(index, reading, view.selected);
  // A bench headline leads the card with the campaign's pick, so the highlight starts on it.
  const seed = headline ? head.nodeFor("winner") : head.own;
  if (seed && !view.seeded) view.select(seed);
  const invalidated = useInvalidated(index);
  const withdrawn = reading !== null && invalidated.has(reading.candidate_id);
  return (
    <li className="cmp-channel">
      <ChannelIdRow
        item={item}
        run={run}
        ink={channelInk(item, points)?.ink ?? null}
        spec={spec}
        withdrawn={withdrawn}
      />
      <ChannelNotes
        reading={reading}
        withdrawn={withdrawn}
        rulerNote={rulerNoteOf(item, ownNote) && !headline}
      />
      <details className="cmp-channel-detail" open={view.folds.detail}>
        <summary
          onClick={(e) => {
            e.preventDefault();
            view.toggleFold("detail");
          }}
        >
          {reading === null ? "Lineage" : "Search reading, lineage and configuration"}
        </summary>
        {view.folds.detail && <ChannelDetail {...props} />}
      </details>
    </li>
  );
}

function ChannelIdRow({
  item: { channel, reading, headline },
  run,
  ink,
  spec,
  withdrawn,
}: {
  item: CompareItem;
  run: HierarchyCampaign | null;
  ink: string | null;
  spec: MetricFormat & { axis_label: string };
  withdrawn: boolean;
}) {
  const { remove } = useCompareSelection();
  const subject = channel.subject;
  const level = channelLevel(reading, withdrawn, spec);
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
              : (run?.label ?? channel.rootCampaignId)}
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
            <Metric label={spec.axis_label} value={level.value} band={level.band} />
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
      {headline && <HeadlineFacts row={headline} />}
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
        <Said
          tone="warn"
          label="A setting was changed at or above the point this channel reads, and nothing ran under it. Every number here is unknown until it is measured — restore it below, or steer & fork from that searchpoint to actually run it."
        />
      )}
      {rulerNote && <Said tone="warn" label={reading.comparable_note} />}
      {reading.human_intervened && (
        <Said
          tone="warn"
          label="An operator intervened mid-run on this cycle, so it is no longer purely reproducible."
        />
      )}
    </>
  );
}
