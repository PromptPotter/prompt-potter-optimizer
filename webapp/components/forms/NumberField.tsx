"use client";

import { useAppliableField } from "@/lib/useAppliableField";

export function NumberField({
  label,
  value,
  min,
  max,
  onApply,
}: {
  label: string;
  value: number;
  min?: number;
  max?: number;
  onApply: (value: number) => void;
}) {
  // Buffer the numeric value as a string so partial / empty edits don't
  // round-trip a NaN; parse + guard on Apply.
  const { local, setLocal, dirty } = useAppliableField(String(value));
  return (
    <label className="new-campaign-field">
      <span>{label}</span>
      <span style={{ display: "flex", gap: "0.5rem" }}>
        <input
          type="number"
          value={local}
          min={min}
          max={max}
          step={1}
          onChange={(e) => setLocal(e.target.value)}
        />
        <button
          type="button"
          disabled={!dirty || Number.isNaN(parseInt(local, 10))}
          onClick={() => onApply(parseInt(local, 10))}
        >
          Apply
        </button>
      </span>
    </label>
  );
}
