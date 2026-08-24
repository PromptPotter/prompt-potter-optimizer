"use client";
import { useState } from "react";
import { SegmentedControl } from "@/components/ui";
import { promptFieldLabel } from "@/lib/prompt-fields";
import { fmtValue } from "@/lib/format";

// What `l1_generate` PRODUCED this round, one candidate at a time.
//
// The node's response is `{variants: [...]}` — the round's whole population in one
// blob — and the node detail used to print it as a single `<pre>`. At three variants
// carrying a citation, a targeted cluster and three override maps each, that is the
// one place on the dashboard where the optimizer's actual reasoning lands, rendered
// as the least readable thing on screen. One variant at a time, fields named.
//
// The shape is the LLM's structured output as the audit twin banked it, not a served
// model, so every field is read defensively: a variant missing one renders without it
// rather than blanking the pane.

interface Variant {
  evidence_grounding?: { field?: string; citation?: string } | null;
  targets_cluster?: string | null;
  pipeline_params_override?: Record<string, unknown> | null;
  prompt_fields_override?: Record<string, unknown> | null;
  task_context_override?: Record<string, unknown> | null;
  changes_description?: string | null;
}

// The variants of a `l1_generate` response, or null when this block is not one.
// Null and an empty list are different answers and the caller renders them apart.
export function variantsOf(response: unknown): Variant[] | null {
  if (!response || typeof response !== "object") return null;
  const v = (response as Record<string, unknown>).variants;
  return Array.isArray(v) ? (v as Variant[]) : null;
}

// An override map worth showing. `{}` is the common case — the optimizer changed the
// prompt but not the params — and an empty table reads as "this axis has no values".
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
  // Index as the axis: the variants carry no id of their own, and their POSITION is
  // what the candidate labels (`C{round}.{idx}`) are minted from.
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

      <Overrides title="Prompt changes" map={shown.prompt_fields_override} />
      <Overrides title="Param changes" map={shown.pipeline_params_override} />
      <Overrides title="Task context" map={shown.task_context_override} />
    </section>
  );
}
