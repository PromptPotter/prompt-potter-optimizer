import { useMemo } from "react";
import type { Evidence, SubjectReading } from "@/lib/api";
import { readingPath } from "@/lib/api/reads";
import type { CladogramChannel } from "@/components/candidates/Forest";
import { useCompareSelection } from "@/lib/state/compare-selection";
import type { HierarchyEntry } from "@/lib/api";
import { campaignAt, compareItems } from "@/lib/derivations";
import { encodeCyclePath } from "@/lib/ids";
import { seriesVar } from "@/lib/theme";
import { ChannelCard } from "./ChannelCard";
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
  byAddress,
}: {
  evidence: Evidence;
  byAddress: ReadonlyMap<string, HierarchyEntry>;
}) {
  // Read off the request, not the response, so a channel that answered nothing still gets a card.
  const { channels } = useCompareSelection();
  const items = useMemo(() => compareItems(evidence, channels), [evidence, channels]);
  const listNote = evidence.comparability.roster_note;
  const points = useMemo(() => channelPoints(evidence.subjects), [evidence.subjects]);
  const h2h = evidence.head_to_head;
  return (
    <>
      {h2h && <HeadToHeadVerdict h2h={h2h} scorerId={evidence.scorer_id} />}
      {listNote !== null && (
        <p className="note-info">
          <strong>Every channel below</strong> — {listNote}
        </p>
      )}
      <ol className="cmp-channels">
        {items.map((item) => (
          <ChannelCard
            key={item.channel.subject}
            item={item}
            ownNote={listNote === null}
            points={points}
            spec={evidence.metric.spec}
            run={campaignAt(byAddress, item.channel.rootCampaignId)}
          />
        ))}
      </ol>
    </>
  );
}
