"use client";

import type { ProvenanceSource, ProvenanceTag } from "@/lib/api";

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

// Plain-language source labels — `auto` is a smart default the operator can
// override; `stated` is a choice they made. Audit sub-tag, only shown once a
// field has a value (.impeccable register: no jargon, accessibility-first).
const SOURCE_LABEL: Record<ProvenanceSource, string> = {
  auto: "auto",
  stated: "you set",
};

export function SourceBadge({ source }: { source: ProvenanceSource | undefined }) {
  if (!source) return null;
  return (
    <span className={`origin-src origin-src--${source}`}>{SOURCE_LABEL[source]}</span>
  );
}
