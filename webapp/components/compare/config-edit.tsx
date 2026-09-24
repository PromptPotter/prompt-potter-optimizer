"use client";
// Configuration edits on Compare. Nothing ever ran at an edited value, so an edit INVALIDATES (a "?")
// rather than re-projects; one map owned by `ComparePane` feeds both the card and the table editor.

import type { SubjectReading } from "@/lib/api";
import { candidateSubject, readingPath } from "@/lib/api/reads";
import { overlayEdits } from "@/lib/derivations";
import { CommitInput } from "@/components/ui";
import { cx } from "@/lib/cx";

/** Keyed by the edited POINT's subject address, not the channel: a channel can walk its branch, and
 *  descendants for invalidation are taken from edited points. */
export type ScenarioEdits = ReadonlyMap<string, ReadonlyMap<string, string>>;

/** The point the server RESOLVED a channel to — a `campaign:`/`course:` channel's address names a branch. */
export function pointKeyOf(reading: SubjectReading): string {
  return candidateSubject(readingPath(reading), reading.candidate_id);
}

/** The emission is the point's WHOLE running config, diffed against its seed (`overlayEdits`); it
 *  replaces rather than merges, so a cleared value is not stranded. */
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

// A key this channel does not carry is NOT editable: no fork could express the edit.
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
