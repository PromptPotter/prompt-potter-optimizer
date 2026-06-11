// Build the draft `pipeline_overlay` patch for ONE node from the lock editor's
// per-param state — the generalization of the old single-node `buildOverlay`
// (model + thinking only) to every node and every param.
//
// The overlay is the transport the operator's optimizer-search-space choices
// ride on (`nodes.{n}.{config, optimizer}`); at mint the backend splits it into
// the per-campaign `pipeline_overrides` (config values) + `optimizer_narrowing`
// (the param-lock / allowed-values subset) — see `launcher.split_overlay`. The
// server replaces the overlay wholesale, so we send the full merged object.
//
// Pure data→data (no React, no I/O) so it rides the lib Vitest scope.

import type { DraftPatch } from "@/lib/api";

// The editor's resolved state for one config param of a node.
export interface ParamState {
  key: string;
  // "model" | "enum" | "number" | "bool" | "string" — drives coercion + which
  // levers apply (model rides `lock_model`; enums carry a narrowed allow-set).
  kind: string;
  // The optimizer may NOT move this param (it leaves the node's `param_keys`).
  // Ignored for `model`, whose lock is the campaign-wide `lock_model` bit.
  locked: boolean;
  // For enum params: the allowed-values subset the optimizer may use when open.
  allowed: string[];
  // The full declared value set (enum/model) — a strict subset of it narrows.
  options: string[];
  // The current (editable) origin value, stringified for the input.
  value: string;
  // The schema's value — a change vs this lands the param in the config overlay.
  baseValue: string;
}

function coerce(kind: string, raw: string): unknown {
  if (kind === "number") {
    const n = Number(raw);
    return Number.isFinite(n) ? n : raw;
  }
  if (kind === "bool") return raw === "true";
  return raw;
}

// Merge this node's state onto the draft's existing overlay and return the patch.
// `param_keys` = the open (unlocked) non-model params — the optimizer's tunable
// subset for this node; `param_allowed_values` narrows an open enum only when it
// is a strict subset of its options; `config` carries changed origin values
// (incl. the chosen model). `lock_model` rides the top-level patch field.
export function nodeOverlayPatch(
  base: Record<string, unknown>,
  lockModel: boolean,
  node: string,
  states: ParamState[],
): DraftPatch {
  const overlay = JSON.parse(JSON.stringify(base ?? {})) as Record<string, Record<string, unknown>>;
  const config: Record<string, unknown> = {};
  const paramKeys: string[] = [];
  const allowedValues: Record<string, string[]> = {};

  for (const s of states) {
    if (s.kind !== "model" && !s.locked) {
      paramKeys.push(s.key);
      if (s.kind === "enum" && s.allowed.length > 0 && s.allowed.length < s.options.length) {
        allowedValues[s.key] = s.allowed;
      }
    }
    if (s.value !== s.baseValue && s.value !== "") {
      config[s.key] = coerce(s.kind, s.value);
    }
  }

  const prev = (overlay[node] ?? {}) as Record<string, unknown>;
  const prevConfig = (prev.config ?? {}) as Record<string, unknown>;
  overlay[node] = {
    ...prev,
    optimizer: { param_keys: paramKeys, param_allowed_values: allowedValues },
    ...(Object.keys(config).length > 0 ? { config: { ...prevConfig, ...config } } : {}),
  };
  return { lock_model: lockModel, pipeline_overlay: overlay };
}
