"use client";

import { useAppliableField } from "@/lib/useAppliableField";

export function SlugField({
  slug,
  onApply,
}: {
  slug: string;
  onApply: (slug: string) => void;
}) {
  const { local, setLocal, dirty } = useAppliableField(slug);
  return (
    <label className="new-campaign-field">
      <span>Slug</span>
      <span style={{ display: "flex", gap: "0.5rem" }}>
        <input
          type="text"
          value={local}
          onChange={(e) => setLocal(e.target.value)}
          pattern="^[a-z][a-z0-9_-]*$"
        />
        <button type="button" disabled={!dirty} onClick={() => onApply(local)}>
          Apply
        </button>
      </span>
    </label>
  );
}
