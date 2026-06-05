"use client";

import type { ProvenanceTag } from "@/lib/api";

const PROVENANCE_LABEL: Record<ProvenanceTag, string> = {
  unset: "Not set",
  proposed: "Proposed",
  confirmed: "Confirmed",
};

export function ProvenanceBadge({ tag }: { tag: ProvenanceTag }) {
  return (
    <span className={`origin-prov origin-prov--${tag}`}>{PROVENANCE_LABEL[tag]}</span>
  );
}
