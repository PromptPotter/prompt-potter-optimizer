"use client";

import { useState } from "react";
import type { DraftCampaignWire, DraftPatch, OptimizerLocks } from "@/lib/api";
import { primaryNode } from "@/lib/optimizer-locks";

// The editable pipeline/optimizer config — the operator decides what the
// optimizer may move. Model lock on/off, which models it may test, which
// thinking levels it may use. Non-demo edits persist (lock_model + a merged
// pipeline_overlay); in demo they stay local and reset when the campaign
// starts. Prefilled from the connector-derived locks.

const THINKING = ["low", "medium", "high"] as const;

interface NodeOverlay {
  config?: Record<string, unknown>;
  optimizer?: {
    param_keys?: string[];
    param_allowed_values?: Record<string, string[]>;
    [k: string]: unknown;
  };
  [k: string]: unknown;
}

function nodeConfig(locks: OptimizerLocks, node: string | null): Record<string, unknown> {
  return node ? (locks.nodes[node]?.config ?? {}) : {};
}

function allowedValues(locks: OptimizerLocks, node: string | null, param: string): string[] {
  return node ? (locks.nodes[node]?.param_allowed_values?.[param] ?? []) : [];
}

// Merge the operator's choices onto the draft's existing pipeline_overlay. The
// server replaces the overlay wholesale, so we send the full merged object.
function buildOverlay(
  base: Record<string, unknown>,
  node: string,
  opts: { model: string; allowedModels: string[]; allowedThinking: string[]; lockModel: boolean },
): Record<string, unknown> {
  const overlay = JSON.parse(JSON.stringify(base)) as Record<string, NodeOverlay>;
  const n: NodeOverlay = overlay[node] ?? {};
  const config = { ...(n.config ?? {}) };
  if (opts.model) config.model = opts.model;
  const optimizer = { ...(n.optimizer ?? {}) };
  const pav: Record<string, string[]> = { ...(optimizer.param_allowed_values ?? {}) };
  pav.reasoning_effort = opts.allowedThinking;
  if (opts.lockModel) {
    delete pav.model;
  } else {
    pav.model = opts.allowedModels;
    const keys = new Set(optimizer.param_keys ?? []);
    keys.add("model");
    optimizer.param_keys = [...keys];
  }
  optimizer.param_allowed_values = pav;
  overlay[node] = { ...n, config, optimizer };
  return overlay as Record<string, unknown>;
}

export function PipelineConfigEditor({
  draft,
  demo,
  onApply,
}: {
  draft: DraftCampaignWire;
  demo: boolean;
  onApply: (patch: DraftPatch) => void;
}) {
  const locks = draft.optimizer_locks;
  const node = primaryNode(locks);
  const cfg = nodeConfig(locks, node);
  const baseModel = typeof cfg.model === "string" ? cfg.model : "";
  const activeThinking = typeof cfg.reasoning_effort === "string" ? cfg.reasoning_effort : "low";

  const [lockModel, setLockModel] = useState(draft.lock_model);
  const [models, setModels] = useState<string[]>(() => {
    const allowed = allowedValues(locks, node, "model");
    return allowed.length > 0 ? allowed : baseModel ? [baseModel] : [];
  });
  const [thinking, setThinking] = useState<string[]>(() => {
    const allowed = allowedValues(locks, node, "reasoning_effort").filter((v) =>
      (THINKING as readonly string[]).includes(v),
    );
    return allowed.length > 0 ? allowed : [activeThinking];
  });

  // Persist on every change — never in demo (resets on Start). lock_model is a
  // top-level toggle; models + thinking ride a merged pipeline_overlay.
  const persist = (next: { lockModel: boolean; models: string[]; thinking: string[] }) => {
    if (demo || !node) return;
    onApply({
      lock_model: next.lockModel,
      pipeline_overlay: buildOverlay(draft.pipeline_overlay, node, {
        model: next.models[0] ?? baseModel,
        allowedModels: next.models,
        allowedThinking: next.thinking,
        lockModel: next.lockModel,
      }),
    });
  };

  const toggleLock = () => {
    const v = !lockModel;
    setLockModel(v);
    persist({ lockModel: v, models, thinking });
  };

  const toggleThinking = (level: string) => {
    const next = thinking.includes(level)
      ? thinking.filter((t) => t !== level)
      : [...thinking, level];
    if (next.length === 0) return; // keep at least one level allowed
    setThinking(next);
    persist({ lockModel, models, thinking: next });
  };

  const commitModels = (raw: string) => {
    const next = raw
      .split(",")
      .map((s) => s.trim())
      .filter(Boolean);
    setModels(next);
    persist({ lockModel, models: next, thinking });
  };

  return (
    <div className="config-editor">
      <div className="config-row">
        <span className="config-label">Pipeline</span>
        <span className="config-value">
          {locks.pipeline.length > 0 ? (
            locks.pipeline.map((s) => (
              <span key={s} className="opt-locks-chip">
                {s}
              </span>
            ))
          ) : (
            <span className="opt-locks-chip">backend default</span>
          )}
        </span>
      </div>

      <div className="config-row">
        <span className="config-label">Model</span>
        <span className="config-value config-model">
          <button
            type="button"
            className={`config-lock${lockModel ? " is-locked" : ""}`}
            onClick={toggleLock}
            aria-pressed={lockModel}
            title={
              lockModel
                ? "Locked — the optimizer keeps this model fixed. Click to let it test others."
                : "Open — the optimizer may test the models below. Click to lock."
            }
          >
            {lockModel ? "🔒 Locked" : "🔓 Open"}
          </button>
          <input
            type="text"
            className="config-input"
            defaultValue={models.join(", ")}
            aria-label={lockModel ? "Model" : "Models to test (comma-separated)"}
            placeholder="openai/gpt-oss-20b"
            onBlur={(e) => commitModels(e.target.value)}
          />
        </span>
      </div>
      <small className="config-hint">
        {lockModel
          ? "Locked — the optimizer keeps this one model. Click 🔒 to let it test several."
          : "Open — the optimizer may swap among these models (comma-separated)."}
      </small>

      <div className="config-row">
        <span className="config-label">Thinking</span>
        <span className="config-value">
          {THINKING.map((level) => {
            const on = thinking.includes(level);
            const isOrigin = level === activeThinking;
            return (
              <button
                key={level}
                type="button"
                className={`config-level${on ? " is-on" : " is-off"}${isOrigin ? " is-origin" : ""}`}
                onClick={() => toggleThinking(level)}
                aria-pressed={on}
                title={
                  isOrigin
                    ? "Origin level (the starting floor) — click to allow/lock out"
                    : on
                      ? "Allowed — click to lock the optimizer out of this level"
                      : "Locked out — click to allow"
                }
              >
                {level}
                {isOrigin ? <span className="config-origin-tag">origin</span> : null}
              </button>
            );
          })}
        </span>
      </div>
      <small className="config-hint">
        Filled = the optimizer may use it · dashed = locked out · “origin” = the starting level.
        Click any to toggle.
      </small>
    </div>
  );
}
