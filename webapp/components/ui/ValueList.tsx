"use client";
import { useState } from "react";
import { cx } from "@/lib/cx";
import { CommitInput } from "./CommitInput";
import { Popover } from "./Popover";
import s from "./ValueList.module.css";

// The one widget for an enumerable axis: position 1 is the start value, the ticks are the
// permitted set, each drawn only when its callback/prop is passed (webapp/CLAUDE.md § an AXIS).
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
  name: string;
  /** ALREADY ordered current-first by the caller; this component never sorts. */
  values: readonly string[];
  /** `undefined` = no tick column, never a column of empty boxes reading "nothing permitted". */
  checked?: readonly string[];
  /** Values something downstream refuses; disabled rather than hidden, since the axis still declares them. */
  inert?: readonly string[];
  /** Values the operator typed that the catalogue does not offer — tagged, as the likeliest typos. */
  userAdded?: readonly string[];
  note?: string;
  readOnly?: boolean;
  /** Omit to drop the free-text row — an axis whose values a caller cannot widen. */
  addPlaceholder?: string;
  /** Omit where the start value is not this list's to set; the rows then render as text. */
  onPick?: (value: string) => void;
  onToggle?: (value: string) => void;
  onAdd?: (value: string) => void;
}) {
  // The free-text input clears by REMOUNT: `CommitInput` latches what it sent, so a constant ""
  // prop never re-clears it.
  const [added, setAdded] = useState(0);

  const start = values[0] ?? "";
  const permitted = checked === undefined ? null : values.filter((v) => checked.includes(v));
  const count = permitted === null ? null : `${permitted.length}/${values.length}`;
  // A permissions-only host (ticks, no pick) shows the permitted set: its value belongs to a sibling surface.
  const summary =
    permitted !== null && !onPick ? permitted.join(", ") || "(none)" : start || "(unset)";

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
          if (values.includes(value)) onPick?.(value);
          else onAdd?.(value);
          setAdded((n) => n + 1);
        };
        return (
          // A listbox only where a value can be CHOSEN; permissions-only, nothing is selectable.
          <div className={s.panel} role={onPick ? "listbox" : "group"} aria-label={name}>
            {values.map((value, i) => {
              const isStart = i === 0 && onPick !== undefined;
              const isInert = inert?.includes(value) ?? false;
              const isOn = checked?.includes(value);
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
                      // Picking closes the panel; ticking does not, since narrowing is several clicks.
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
                      // An inert value can be UNticked but not ticked: unticking is the repair for a
                      // permitted value something downstream now refuses.
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
