"use client";

import { useId } from "react";
import { useAppliableField } from "@/lib/hooks/useAppliableField";

export function SlugField({
  slug,
  onApply,
}: {
  slug: string;
  onApply: (slug: string) => void;
}) {
  const id = useId();
  const { local, setLocal, dirty } = useAppliableField(slug);
  return (
    <div className="new-campaign-field">
      {/* The label names the INPUT alone — wrapping the button too would fold "Apply" into the
          field's accessible name and let a click on the caption press it. */}
      <label htmlFor={id}>Slug</label>
      <span className="new-campaign-apply">
        <input
          id={id}
          type="text"
          value={local}
          onChange={(e) => setLocal(e.target.value)}
          pattern="^[a-z][a-z0-9_-]*$"
        />
        <button type="button" disabled={!dirty} onClick={() => onApply(local)}>
          Apply
        </button>
      </span>
    </div>
  );
}
