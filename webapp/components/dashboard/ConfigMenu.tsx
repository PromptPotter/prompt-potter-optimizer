"use client";
import { useState } from "react";

// "Very-very hidden" campaign-config menu — placeholder for the future
// editable config surface. MVP: show what the optimizer CANNOT mutate
// (operator-locked axes + value-restricted axes per the dataset's
// pipeline.json::optimizer block). Renders as a small ⚙ button next to
// the Input/Output hero toggles in ChatPane.
//
// Data source: temporarily hardcoded per dataset until /api/v1/campaigns
// /active/config exposes the merged overlay. Update FROZEN_BY_DATASET
// when adding/removing a value-lock; the source of truth for the lock
// itself stays in datasets/{name}/pipeline.json::nodes.{node}.
//
// Roadmap: this component is the seed of a richer config menu —
// spend cap, model selection, optimizer toggles, etc. The future API
// endpoint will return a typed config snapshot; render here.

interface FrozenAxis {
  node: string;
  axis: string;
  lockKind: "axis" | "value";
  current?: string;
  forbidden?: string[]; // present when lockKind == "value"
}

// Hardcoded until the API ships. Reflects:
//   - PARAM_FORBIDDEN_KEYS = {"model", "provider"} (domain.search_point)
//   - datasets/aime_2025/pipeline.json::nodes.llm_only.optimizer.param_allowed_values
const FROZEN_BY_DATASET: Record<string, FrozenAxis[]> = {
  aime_2025: [
    { node: "llm_only", axis: "model", lockKind: "axis", current: "openai/gpt-oss-20b:nitro" },
    { node: "llm_only", axis: "provider", lockKind: "axis", current: "openrouter" },
    {
      node: "llm_only",
      axis: "reasoning_effort",
      lockKind: "value",
      current: "low",
      forbidden: ["medium", "high"],
    },
  ],
};

interface Props {
  datasetName: string | null;
}

export function ConfigMenu({ datasetName }: Props) {
  const [open, setOpen] = useState(false);
  const frozen = (datasetName && FROZEN_BY_DATASET[datasetName]) || [];

  return (
    <div className="config-menu" style={{ position: "relative", display: "inline-flex" }}>
      <button
        type="button"
        className="config-menu-trigger"
        aria-expanded={open}
        aria-label="Campaign config"
        title="Campaign config"
        onClick={() => setOpen((v) => !v)}
        style={{
          width: 28,
          height: 28,
          border: "1px solid var(--border, #2a2a2a)",
          borderRadius: 6,
          background: "transparent",
          color: "currentColor",
          cursor: "pointer",
          opacity: 0.55,
          display: "inline-flex",
          alignItems: "center",
          justifyContent: "center",
        }}
      >
        <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
          <circle cx="8" cy="8" r="2.2" />
          <path d="M8 1.5v1.6M8 12.9v1.6M2.5 8H4.1M11.9 8h1.6M3.6 3.6l1.1 1.1M11.3 11.3l1.1 1.1M3.6 12.4l1.1-1.1M11.3 4.7l1.1-1.1" />
        </svg>
      </button>
      {open && (
        <div
          className="config-menu-panel"
          role="region"
          aria-label="Campaign config"
          style={{
            position: "absolute",
            top: "calc(100% + 6px)",
            right: 0,
            minWidth: 280,
            padding: "10px 12px",
            background: "var(--surface, #111)",
            border: "1px solid var(--border, #2a2a2a)",
            borderRadius: 8,
            boxShadow: "0 8px 24px rgba(0,0,0,0.35)",
            fontSize: 12,
            lineHeight: 1.5,
            zIndex: 50,
          }}
        >
          <div style={{ fontWeight: 600, marginBottom: 6, opacity: 0.85 }}>
            Frozen parameters
          </div>
          {frozen.length === 0 ? (
            <div style={{ opacity: 0.55 }}>No frozen parameters for this campaign.</div>
          ) : (
            <ul style={{ listStyle: "none", padding: 0, margin: 0 }}>
              {frozen.map((f) => (
                <li key={`${f.node}.${f.axis}`} style={{ padding: "4px 0", borderTop: "1px dashed var(--border, #2a2a2a)" }}>
                  <div style={{ display: "flex", alignItems: "baseline", gap: 6 }}>
                    <span style={{ opacity: 0.5 }}>{f.node}.</span>
                    <strong>{f.axis}</strong>
                    <span
                      style={{
                        fontSize: 10,
                        padding: "1px 5px",
                        borderRadius: 4,
                        background: f.lockKind === "axis" ? "rgba(220,80,80,0.18)" : "rgba(180,140,40,0.18)",
                        color: f.lockKind === "axis" ? "#e88" : "#dba",
                      }}
                    >
                      {f.lockKind === "axis" ? "FROZEN" : "value-locked"}
                    </span>
                  </div>
                  {f.current && (
                    <div style={{ opacity: 0.7, marginLeft: 2 }}>
                      current: <code>{f.current}</code>
                    </div>
                  )}
                  {f.forbidden && (
                    <div style={{ opacity: 0.55, marginLeft: 2 }}>
                      blocked: {f.forbidden.map((v) => <code key={v} style={{ marginRight: 4 }}>{v}</code>)}
                    </div>
                  )}
                </li>
              ))}
            </ul>
          )}
          <div style={{ marginTop: 8, opacity: 0.4, fontSize: 10 }}>
            More config knobs land here as the menu grows.
          </div>
        </div>
      )}
    </div>
  );
}
