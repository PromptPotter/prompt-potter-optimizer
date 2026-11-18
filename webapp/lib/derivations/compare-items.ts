import type { Evidence, HeadToHeadRow, SubjectReading } from "@/lib/api/types";

export interface CompareItem {
  subject: string;
  // `null`: asked for and answered nothing (`Evidence.unread_subjects`).
  reading: SubjectReading | null;
  slot: number | null;
  headline: HeadToHeadRow | null;
}

export function compareItems(
  evidence: Evidence,
  subjects: readonly string[],
): CompareItem[] {
  const slots = new Map(evidence.subjects.map((s, i) => [s.key, i]));
  const headlines = new Map((evidence.head_to_head?.rows ?? []).map((r) => [r.subject, r]));
  return subjects.map((subject) => {
    const slot = slots.get(subject);
    const reading = slot === undefined ? undefined : evidence.subjects[slot];
    return slot === undefined || reading === undefined
      ? { subject, reading: null, slot: null, headline: null }
      : { subject, reading, slot, headline: headlines.get(subject) ?? null };
  });
}
