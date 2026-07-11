"use client";

/**
 * One-of-N knob. A discrete choice has no partial state, so it applies on
 * change — no Apply button (that buffering exists for free text / numbers that
 * can be mid-edit and invalid; see NumberField).
 */
export function ChoiceField<T extends string>({
  label,
  value,
  options,
  hint,
  onApply,
}: {
  label: string;
  value: T;
  options: readonly { value: T; label: string }[];
  hint?: string;
  onApply: (value: T) => void;
}) {
  return (
    <label className="new-campaign-field">
      <span>{label}</span>
      <select value={value} onChange={(e) => onApply(e.target.value as T)}>
        {options.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </select>
      {hint ? <small>{hint}</small> : null}
    </label>
  );
}
