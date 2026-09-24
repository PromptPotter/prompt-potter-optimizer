"use client";

// A discrete choice has no partial state, so it applies on change (unlike `CommitInput`).
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
