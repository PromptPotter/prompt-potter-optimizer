import type { Evidence, HeadToHeadRow, SubjectReading } from "@/lib/api/types";
import type { CompareChannel } from "@/lib/compare-selection";

export interface CompareItem {
  channel: CompareChannel;
  // `null`: asked for and answered nothing (`Evidence.unread_subjects`).
  reading: SubjectReading | null;
  slot: number | null;
  headline: HeadToHeadRow | null;
}

export function compareItems(
  evidence: Evidence,
  channels: readonly CompareChannel[],
): CompareItem[] {
  const slots = new Map(evidence.subjects.map((s, i) => [s.key, i]));
  const headlines = new Map((evidence.head_to_head?.rows ?? []).map((r) => [r.subject, r]));
  return channels.map((channel) => {
    const slot = slots.get(channel.subject);
    const reading = slot === undefined ? undefined : evidence.subjects[slot];
    return slot === undefined || reading === undefined
      ? { channel, reading: null, slot: null, headline: null }
      : { channel, reading, slot, headline: headlines.get(channel.subject) ?? null };
  });
}
