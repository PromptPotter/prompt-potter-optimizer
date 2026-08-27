"use client";
// Editing a searchpoint's CONFIGURATION on Compare — the value, the two controls, and what an
// edit does to the numbers beside it.
//
// The whole point of the two mask kinds. A scoring criterion and a sample subset are
// RE-PROJECTIONS of the record: the rows exist, the server re-reads them, and the answer is a
// number. A node parameter, a model or a prompt field is not — nothing ever ran under the edited
// value, so every measurement on that channel describes a different searchpoint and NONE of them
// carries over.
//
// So the honest render of a config edit is a wall of "?" rather than a recomputed anything, and
// this module is the one place that says so. It holds no numbers; it holds which channels are
// asking a question the record cannot answer.
//
// ONE state, two surfaces. The channel card's expandable and the side-by-side table are the same
// editor over the same map (owned by `ComparePane`), which is what keeps them synchronous — an
// edit typed in either shows in both, and the card's `?` follows immediately.

import type { SubjectReading } from "@/lib/api";
import { candidateSubject, readingPath } from "@/lib/api/reads";
import { overlayEdits } from "@/lib/derivations";
import { CommitInput } from "@/components/ui";
import { cx } from "@/lib/cx";

/** Per-SEARCHPOINT overrides, keyed by the edited point's own subject address then by the flat
 *  config key. Keyed on the POINT rather than on the channel because a channel is a window onto a
 *  branch and the operator can walk it: an edit made at R3 while a channel reads at R5 is a
 *  question about R3, and keyed on the channel the two would be one entry. It is also what makes
 *  the invalidation closure expressible — a set of edited points is what descendants are taken
 *  from. */
export type ScenarioEdits = ReadonlyMap<string, ReadonlyMap<string, string>>;

/** The subject address of the searchpoint a channel READS AT — not the channel's own address,
 *  which for a `campaign:` or `course:` channel names a branch rather than the point the server
 *  resolved it to. One spelling, so the card's editor and the table's cells write the same entry
 *  and stay synchronous. */
export function pointKeyOf(reading: SubjectReading): string {
  return candidateSubject(readingPath(reading), reading.candidate_id);
}

/** Take a node-surface emission as this point's WHOLE edit set — DIFFED against the config it was
 *  seeded from (`overlayEdits`), because the emission is the searchpoint's entire running
 *  configuration and not a delta. It replaces rather than merges: the editor is authoritative for
 *  its own point, and a merge would strand a value the operator had just cleared.
 *
 *  The flat spelling is the server's `node.param`, minted by `flatConfigKey` — the same keys the
 *  side-by-side table's cells carry, which is the whole reason the two editors line up. */
export function withOverlay(
  edits: ScenarioEdits,
  pointKey: string,
  emitted: Record<string, Record<string, unknown>>,
  seed: Record<string, unknown>,
): ScenarioEdits {
  const changed = overlayEdits(emitted, seed);
  const next = new Map(edits);
  const keys = Object.keys(changed);
  if (keys.length === 0) next.delete(pointKey);
  else next.set(pointKey, new Map(keys.map((k) => [k, changed[k] as string])));
  return next;
}

export const NO_EDITS: ScenarioEdits = new Map();

export function editsFor(edits: ScenarioEdits, subjectKey: string): ReadonlyMap<string, string> {
  return edits.get(subjectKey) ?? new Map();
}

/** Set one cell, or clear it when `value` matches the served one. Returns a new map. */
export function withEdit(
  edits: ScenarioEdits,
  subjectKey: string,
  configKey: string,
  value: string,
  served: string | undefined,
): ScenarioEdits {
  const next = new Map(edits);
  const channel = new Map(next.get(subjectKey) ?? []);
  if (value === (served ?? "")) channel.delete(configKey);
  else channel.set(configKey, value);
  if (channel.size === 0) next.delete(subjectKey);
  else next.set(subjectKey, channel);
  return next;
}

/** Put one channel — or one of its cells — back on the record. */
export function restored(
  edits: ScenarioEdits,
  subjectKey: string,
  configKey?: string,
): ScenarioEdits {
  const next = new Map(edits);
  if (configKey === undefined) {
    next.delete(subjectKey);
    return next;
  }
  const channel = new Map(next.get(subjectKey) ?? []);
  channel.delete(configKey);
  if (channel.size === 0) next.delete(subjectKey);
  else next.set(subjectKey, channel);
  return next;
}

// One editable value, wherever it is rendered. A key this channel does not carry is NOT editable:
// there is no param to move, and an input would invite an edit no fork could express.
export function ConfigCell({
  name,
  subjectKey,
  label,
  served,
  edits,
  onEdits,
}: {
  name: string;
  subjectKey: string;
  label: string;
  served: string | undefined;
  edits: ScenarioEdits;
  onEdits: (next: ScenarioEdits) => void;
}) {
  if (served === undefined) return <span className="l4-dim">—</span>;
  const edited = editsFor(edits, subjectKey).get(name);
  return (
    <span
      className={cx("cmp-cfg-cell", edited !== undefined && "cmp-cfg-edited")}
      title={edited !== undefined ? `${served} → ${edited}` : served}
    >
      <CommitInput
        className="cmp-cfg-input"
        value={edited ?? served}
        aria-label={`${name} on ${label}`}
        onCommit={(next) => onEdits(withEdit(edits, subjectKey, name, next, served))}
      />
      {edited !== undefined && (
        <button
          type="button"
          className="cmp-link cmp-cfg-restore"
          title={`Back to the recorded value: ${served}`}
          onClick={() => onEdits(restored(edits, subjectKey, name))}
        >
          ↺
        </button>
      )}
    </span>
  );
}

// Silent at zero: an always-present control saying "0 edited" is a restore for nothing.
export function ChannelRestore({
  edits,
  subjectKey,
  onEdits,
}: {
  edits: ScenarioEdits;
  subjectKey: string;
  onEdits: (next: ScenarioEdits) => void;
}) {
  const n = editsFor(edits, subjectKey).size;
  if (n === 0) return null;
  return (
    <button
      type="button"
      className="cmp-link cmp-cfg-restore"
      title="Put every value on this channel back on the record"
      onClick={() => onEdits(restored(edits, subjectKey))}
    >
      ↺ {n} edited
    </button>
  );
}
