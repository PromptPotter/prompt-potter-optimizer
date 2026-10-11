"use client";

import { useMemo, useState, type CSSProperties } from "react";
import type { Evidence, SubjectReading } from "@/lib/api";
import { readingPath } from "@/lib/api/reads";
import type { CladogramChannel } from "@/components/candidates/Forest";
import { useCompareSelection } from "@/lib/compare-selection";
import { compareItems, type RunGroup } from "@/lib/derivations";
import { cx } from "@/lib/cx";
import { encodeCyclePath } from "@/lib/ids";
import { seriesVar } from "@/lib/theme";
import { ChannelCard } from "./ChannelCard";
import { COLUMN_ROWS, ROW_LABEL } from "./ChannelColumn";
import { HeadToHeadVerdict } from "./HeadToHeadVerdict";

function channelPoints(subjects: readonly SubjectReading[]): CladogramChannel[] {
  return subjects.map((s, i) => ({
    coursePathKey: encodeCyclePath(readingPath(s)),
    candidateId: s.candidate_id,
    // Served order: the index the bars, legend and pairwise table read, so one channel is one colour.
    ink: seriesVar(i),
  }));
}

export function ChannelCards({
  evidence,
  runs,
}: {
  evidence: Evidence;
  runs: ReadonlyMap<string, RunGroup>;
}) {
  // Read off the request, not the response, so a channel that answered nothing still gets a card.
  const { channels } = useCompareSelection();
  const items = useMemo(() => compareItems(evidence, channels), [evidence, channels]);
  const listNote = evidence.comparability.roster_note;
  const points = useMemo(() => channelPoints(evidence.subjects), [evidence.subjects]);
  const h2h = evidence.head_to_head;
  const rows = useMemo(() => {
    if (!h2h?.covers_selection) return null;
    const flagged = items.some(
      ({ headline }) =>
        headline && (headline.guard.state !== "controlled" || headline.bench_set === null),
    );
    return COLUMN_ROWS.filter(
      (r) => (r !== "optimizer" || h2h.optimizers_differ) && (r !== "standing" || flagged),
    );
  }, [h2h, items]);
  const [panelHost, setPanelHost] = useState<HTMLDivElement | null>(null);
  const cards = items.map((item, i) => (
    <ChannelCard
      key={item.channel.subject}
      item={item}
      ownNote={listNote === null}
      points={points}
      spec={evidence.metric.spec}
      run={runs.get(item.channel.rootCampaignId) ?? null}
      column={rows && { rows, panelHost, order: i }}
    />
  ));
  return (
    <>
      {h2h && <HeadToHeadVerdict h2h={h2h} scorerId={evidence.scorer_id} />}
      {listNote !== null && (
        <p className="note-info">
          <strong>Every channel below</strong> — {listNote}
        </p>
      )}
      {rows ? (
        <>
          <div
            className="cmp-cols"
            style={{ "--cmp-rows": rows.length } as CSSProperties}
            role="group"
            aria-label="Campaigns side by side"
          >
            <div className="cmp-col">
              {rows.map((r) => (
                <div key={r} className={cx("cmp-cell", "cmp-row-label", `is-${r}`)}>
                  {ROW_LABEL[r]}
                </div>
              ))}
            </div>
            {cards}
          </div>
          <div className="cmp-col-panels" ref={setPanelHost} />
        </>
      ) : (
        <ol className="cmp-channels">{cards}</ol>
      )}
    </>
  );
}
