import { useMemo } from "react";
import type { Evidence, SubjectReading, HierarchyEntry } from "@/lib/api/types";
import type { CladogramChannel } from "@/components/candidates/Forest";
import { useCompareSelection } from "@/lib/state/compare-selection";
import { subjectRoot } from "@/lib/api/reads";
import { campaignAt, compareItems, readingAddress } from "@/lib/derivations";
import { seriesVar } from "@/lib/theme";
import { ChannelCard } from "./ChannelCard";
import { HeadToHeadVerdict } from "./HeadToHeadVerdict";

function channelPoints(subjects: readonly SubjectReading[]): CladogramChannel[] {
  return subjects.map((s, i) => ({
    address: readingAddress(s),
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
  const { subjects } = useCompareSelection();
  const items = useMemo(() => compareItems(evidence, subjects), [evidence, subjects]);
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
            key={item.subject}
            item={item}
            ownNote={listNote === null}
            points={points}
            spec={evidence.metric.spec}
            run={campaignAt(byAddress, subjectRoot(item.subject))}
          />
        ))}
      </ol>
    </>
  );
}
