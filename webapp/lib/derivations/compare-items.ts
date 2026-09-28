// The Compare list's items: one per requested channel, whatever the subject kind — a seed, a
// branch, a searchpoint or a campaign. A campaign's bench headline is its served head-to-head row.

import type { Evidence, HeadToHeadRow, SubjectReading } from "@/lib/api/types";
import type { CompareChannel } from "@/lib/compare-selection";

export interface CompareItem {
  channel: CompareChannel;
  // `null`: asked for and answered nothing (`Evidence.unread_subjects`).
  reading: SubjectReading | null;
  // The served order the charts' inks follow; `null` with no reading.
  slot: number | null;
  // `null` wherever the head-to-head read no row: a searchpoint, a masked campaign, a second
  // subject of one campaign.
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

// Campaign COLUMNS when every item carries a served head-to-head row — campaigns only; otherwise
// null and the list stays per-searchpoint. The optimizer row draws only where the rows differ on it.
export function campaignColumns(
  items: readonly CompareItem[],
): { showOptimizer: boolean } | null {
  const rows = items.flatMap((i) => (i.headline ? [i.headline] : []));
  if (rows.length === 0 || rows.length < items.length) return null;
  return { showOptimizer: new Set(rows.map((r) => r.optimizer)).size > 1 };
}

// The served `comparable_note` when every read item carries the same one: a fact about the whole
// list, said once above it rather than on each item.
export function sharedComparableNote(items: readonly CompareItem[]): string | null {
  const notes = new Set<string>();
  for (const { reading } of items) {
    if (reading === null) continue;
    if (reading.comparable !== false) return null;
    notes.add(reading.comparable_note);
  }
  const [only] = notes;
  return notes.size === 1 && only !== undefined ? only : null;
}
