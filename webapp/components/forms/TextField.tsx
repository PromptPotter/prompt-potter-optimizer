"use client";

import { useAppliableField } from "@/lib/hooks/useAppliableField";

export function TextField({
  label,
  value,
  placeholder,
  onApply,
}: {
  label: string;
  value: string;
  placeholder?: string;
  onApply: (value: string) => void;
}) {
  const { local, setLocal, dirty } = useAppliableField(value);
  return (
    <label className="new-campaign-field">
      <span>{label}</span>
      <textarea
        value={local}
        placeholder={placeholder}
        onChange={(e) => setLocal(e.target.value)}
        rows={3}
      />
      <button type="button" disabled={!dirty} onClick={() => onApply(local)}>
        Apply
      </button>
    </label>
  );
}
