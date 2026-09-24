"use client";
import { useState } from "react";
import { SegmentedControl } from "@/components/ui";
import { promptFieldLabel } from "@/lib/prompt-fields";
import { fmtValue } from "@/lib/format";

// What `l1_generate` produced this round, one candidate at a time. The shape is the LLM's output
// as the audit twin banked it, not a served model, so every field is read defensively.

interface Variant {
  evidence_grounding?: { field?: string; citation?: string } | null;
  targets_cluster?: string | null;
  pipeline_overlay?: Record<string, unknown> | null;
  prompt_fields_updates?: Record<string, unknown> | null;
  task_context_updates?: Record<string, unknown> | null;
  changes_description?: string | null;
}

// Null (not an `l1_generate` block) and `[]` are different answers; the caller renders them apart.
export function variantsOf(response: unknown): Variant[] | null {
  if (!response || typeof response !== "object") return null;
  const v = (response as Record<string, unknown>).variants;
  return Array.isArray(v) ? (v as Variant[]) : null;
}

function Overrides({ title, map }: { title: string; map: Record<string, unknown> | null | undefined }) {
  const entries = Object.entries(map ?? {}).filter(([, v]) => {
    if (v == null || v === "") return false;
    if (typeof v === "object") return Object.keys(v as object).length > 0;
    return true;
  });
  if (entries.length === 0) return null;
  return (
    <div className="l1v-block">
      <span className="l1v-block-head">{title}</span>
      <dl className="l1v-fields">
        {entries.map(([k, v]) => (
          <div key={k} className="l1v-field">
            <dt>{promptFieldLabel(k)}</dt>
            <dd>
              <pre>{fmtValue(v, { pretty: true })}</pre>
            </dd>
          </div>
        ))}
      </dl>
    </div>
  );
}

export function L1Variants({ variants }: { variants: Variant[] }) {
  // Index is the key: a variant's POSITION is what its candidate label (`C{round}.{idx}`) is minted from.
  const [pick, setPick] = useState("0");
  const idx = Number(pick);
  const shown = variants[idx] ?? variants[0];
  if (!shown) return null;

  const grounding = shown.evidence_grounding ?? null;

  return (
    <section className="l1-variants" aria-label="Generated candidates">
      <div className="l1v-head">
        <span className="l1v-title">Candidates</span>
        <SegmentedControl
          options={variants.map((_, i) => ({
            value: String(i),
            label: `.${i + 1}`,
            title: `Candidate ${i + 1} of ${variants.length}`,
          }))}
          value={String(Math.min(idx, variants.length - 1))}
          onChange={setPick}
          ariaLabel="Which generated candidate to show"
        />
        <span className="l1v-count">
          {variants.length} this round
        </span>
      </div>

      {shown.changes_description && (
        <p className="l1v-lede">{shown.changes_description}</p>
      )}

      {grounding && (grounding.field || grounding.citation) && (
        <div className="l1v-block">
          <span className="l1v-block-head">
            Grounded in{grounding.field ? ` · ${grounding.field}` : ""}
          </span>
          {grounding.citation && <blockquote className="l1v-cite">{grounding.citation}</blockquote>}
        </div>
      )}

      {shown.targets_cluster && (
        <div className="l1v-block">
          <span className="l1v-block-head">Targets</span>
          <p className="l1v-targets">{shown.targets_cluster}</p>
        </div>
      )}

      <Overrides title="Prompt changes" map={shown.prompt_fields_updates} />
      <Overrides title="Param changes" map={shown.pipeline_overlay} />
      <Overrides title="Task context" map={shown.task_context_updates} />
    </section>
  );
}
