"use client";

import { useId } from "react";
import { useAppliableField } from "@/lib/hooks/useAppliableField";

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
  const id = useId();
  // Buffer the numeric value as a string so partial / empty edits don't
  // round-trip a NaN; parse + guard on Apply.
  const { local, setLocal, dirty } = useAppliableField(String(value));
  return (
    <div className="new-campaign-field">
      {/* The label names the INPUT alone — wrapping the button too would fold "Apply" into the
          field's accessible name and let a click on the caption press it. */}
      <label htmlFor={id}>{label}</label>
      <span className="new-campaign-apply">
        <input
          id={id}
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
    </div>
  );
}
