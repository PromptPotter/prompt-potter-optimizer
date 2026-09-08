"use client";
import { useState } from "react";
import { cx } from "@/lib/cx";
import { CommitInput } from "./CommitInput";
import { Popover } from "./Popover";
import s from "./ValueList.module.css";

// ONE widget for every axis whose values are enumerable — a model menu, a reasoning ladder, any
// enum. Domain-free on purpose: the caller names the axis and decides what a tick licenses, so
// the vocabulary stays in the surface and this stays a primitive.
//
// **Closed it is ONE LINE; open, the list drops OVER that line.** The dropdown-LIST idiom rather
// than the combo box: the panel covers the closed row (`Popover side="over"`), so the current
// value is on screen exactly once — as the panel's first entry, landing where the closed line
// sat. A panel hanging BELOW would leave the trigger visible beside it and print the value twice.
// Inline disclosure was the first shape and is what this replaced: it pushed every row under it
// down the page each time an axis was opened.
//
// **Two facts, and neither needs a marker of its own.**
//   - WHERE THE AXIS STARTS is position 1. Clicking a value pulls it to the front, and that IS
//     choosing it. No radio, no filled dot: a second control for a fact the order already carries
//     is how a list comes to contradict itself.
//   - WHAT IS PERMITTED is the tick. One tick pins the axis; more than one opens it.
//
// **Each fact is present exactly when its channel is** — `onPick` absent drops the start (values
// become text, not buttons), `checked` absent drops the tick column. A host that owns only one of
// the two says so by passing only one, and gets no control it cannot honour.
//
// State here is one scalar: a remount counter for the free-text input. `values`, `checked` and the
// order are props end to end, so the caller's store is the single source of truth and every
// gesture patches it — which is what makes the no-duplicate rule structural rather than a
// synchronization problem. Open/closed belongs to `Popover`, which already owns click-outside
// and Escape.
export function ValueList({
  name,
  values,
  checked,
  inert,
  userAdded,
  note,
  readOnly = false,
  addPlaceholder,
  onPick,
  onToggle,
  onAdd,
}: {
  /** The axis, as the operator reads it — a `snake_case` param key, usually. */
  name: string;
  /** The full menu, ALREADY ordered current-first by the caller. `values[0]` is the origin —
   *  where the axis starts — but only where `onPick` makes that a fact this list owns. */
  values: readonly string[];
  /** The permitted subset. `undefined` = no tick column at all, rather than a column of empty
   *  boxes that would read as "nothing permitted". */
  checked?: readonly string[];
  /** Values something downstream refuses (a picked model that does not take this rung). Disabled
   *  rather than hidden: the axis still declares them, and they come back when the refusal does
   *  not apply — dropping them would make that reappearance look like a schema change. */
  inert?: readonly string[];
  /** Values the OPERATOR typed in, which neither the catalogue nor the current selection
   *  offered. Tagged rather than silently mixed in: a name nothing else vouches for is the one
   *  most likely to be a typo, and after a round-trip it is otherwise indistinguishable from a
   *  value the admin declared. */
  userAdded?: readonly string[];
  /** One served line: who the ticks license, or why a value is inert. */
  note?: string;
  readOnly?: boolean;
  /** Omit to drop the free-text row — an axis whose values a caller cannot widen. */
  addPlaceholder?: string;
  /** Omit where the START VALUE is not this list's to set — a permissions-only host, whose
   *  sibling surface owns the value. The rows then render as text: an enabled control that
   *  discards the click is the shape this absence exists to make unspellable. */
  onPick?: (value: string) => void;
  onToggle?: (value: string) => void;
  onAdd?: (value: string) => void;
}) {
  // The free-text input clears by REMOUNT: `CommitInput` latches what it sent, so a constant ""
  // prop never re-clears it. Bumping the key is the whole mechanism.
  const [added, setAdded] = useState(0);

  const start = values[0] ?? "";
  const permitted = checked === undefined ? null : values.filter((v) => checked.includes(v));
  const count = permitted === null ? null : `${permitted.length}/${values.length}`;
  // Closed, the line states the fact this list OWNS. Printing `values[0]` on a permissions-only
  // host would read as a start value on a surface that cannot set one, so there the line is the
  // permitted set itself — which for a pinned axis is the same single value, correctly.
  const summary = onPick ? start || "(unset)" : (permitted ?? []).join(", ") || "(none)";

  return (
    <Popover
      className={s.wrap}
      side="over"
      renderTrigger={({ open, toggle }) => (
        <button
          type="button"
          className={s.trigger}
          onClick={toggle}
          aria-expanded={open}
          aria-haspopup={onPick ? "listbox" : true}
          aria-label={`${name}: ${summary}`}
        >
          <span className={s.triggerValue}>{summary}</span>
          {count ? <span className={s.count}>{count}</span> : null}
          <span className={s.caret} aria-hidden="true">
            ▾
          </span>
        </button>
      )}
    >
      {({ close }) => {
        const commitAdd = (raw: string) => {
          const value = raw.trim();
          if (!value) return;
          // Already on the menu: pick it. Idempotent-and-useful beats a dead no-op — an operator
          // who typed a name that is already there meant "use this one".
          if (values.includes(value)) onPick?.(value);
          else onAdd?.(value);
          setAdded((n) => n + 1);
        };
        return (
          // A listbox only where a value can be CHOSEN. Permissions-only, the rows are labels
          // for their checkboxes and nothing is selectable, so it is a group — announcing
          // "listbox, option 1 of 5, selected" over text nobody can pick is a lie to a reader
          // who cannot see that it does not respond.
          <div className={s.panel} role={onPick ? "listbox" : "group"} aria-label={name}>
            {values.map((value, i) => {
              const isStart = i === 0 && onPick !== undefined;
              const isInert = inert?.includes(value) ?? false;
              const isOn = checked?.includes(value);
              // The value the axis starts on cannot be ruled out — nothing would be left to run.
              const lastTick = isOn === true && permitted?.length === 1;
              return (
                <div
                  key={value}
                  className={cx(
                    s.row,
                    isStart && s.start,
                    isOn === false && s.out,
                    isInert && s.inert,
                  )}
                >
                  {onPick ? (
                    <button
                      type="button"
                      role="option"
                      aria-selected={isStart}
                      className={s.name}
                      disabled={readOnly || isStart || isInert}
                      title={
                        isInert
                          ? "Not available on the current selection"
                          : isStart
                            ? `${name} starts here`
                            : `Start ${name} on ${value}`
                      }
                      // Picking is the whole gesture, so the panel closes on it. Ticking does
                      // not: narrowing a permitted set is several clicks, and a panel that shut
                      // on each would make the operator re-open it per value.
                      onClick={() => {
                        onPick(value);
                        close();
                      }}
                    >
                      <ValueLabel value={value} userAdded={userAdded} />
                    </button>
                  ) : (
                    <span className={s.name}>
                      <ValueLabel value={value} userAdded={userAdded} />
                    </span>
                  )}
                  {isOn === undefined ? null : (
                    <input
                      type="checkbox"
                      className={s.tick}
                      checked={isOn}
                      // An inert value can be UNticked but not ticked: unticking it is the repair
                      // for a permitted value something downstream now refuses, and disabling
                      // that would leave the operator looking at a state they cannot leave.
                      disabled={readOnly || (isInert && !isOn) || lastTick}
                      aria-label={`Permit ${value} for ${name}`}
                      title={
                        lastTick
                          ? "The only permitted value — the axis is pinned here. Tick another first."
                          : isInert && isOn
                            ? "Permitted, but the current selection refuses it — untick to repair"
                            : isOn
                              ? "Permitted — untick to rule it out"
                              : "Ruled out — tick to permit it"
                      }
                      onChange={() => onToggle?.(value)}
                    />
                  )}
                </div>
              );
            })}
            {note ? <div className={s.note}>{note}</div> : null}
            {addPlaceholder && onAdd && !readOnly ? (
              <div className={s.add}>
                <span className={s.addMark} aria-hidden="true">
                  +
                </span>
                <CommitInput
                  key={added}
                  className={s.addInput}
                  value=""
                  placeholder={addPlaceholder}
                  aria-label={`Add a value to ${name}`}
                  onCommit={commitAdd}
                />
              </div>
            ) : null}
          </div>
        );
      }}
    </Popover>
  );
}

// The value and its provenance, identical whether the row is pickable or not — so the two
// branches above differ in what they DO, never in what they say.
function ValueLabel({ value, userAdded }: { value: string; userAdded?: readonly string[] }) {
  return (
    <>
      {value}
      {userAdded?.includes(value) ? (
        <span className={s.tag} title="You added this — nothing in the catalogue vouches for it.">
          user
        </span>
      ) : null}
    </>
  );
}
