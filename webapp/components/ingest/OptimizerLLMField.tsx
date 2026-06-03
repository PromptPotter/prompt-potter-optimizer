"use client";

import { useEffect, useState } from "react";
import { fetchLLMProviders, type LLMProvider } from "@/lib/api";
import { useAppliableField } from "@/lib/hooks/useAppliableField";

// Optimizer-LLM picker. Fetches the curated provider list at mount and
// surfaces availability per provider — providers whose API key isn't
// configured render dimmed with the env-var name the operator needs to set.
// "" model means "use settings.LLM_MODEL fallback" — kept as an explicit
// option so the operator can pick "default" without typing.
export function OptimizerLLMField({
  provider,
  model,
  onApply,
}: {
  provider: string;
  model: string;
  onApply: (provider: string, model: string) => void;
}) {
  const [providers, setProviders] = useState<LLMProvider[] | null>(null);
  const { local: localProvider, setLocal: setLocalProvider, dirty: providerDirty } =
    useAppliableField(provider);
  const { local: localModel, setLocal: setLocalModel, dirty: modelDirty } =
    useAppliableField(model);

  useEffect(() => {
    let cancelled = false;
    fetchLLMProviders()
      .then((r) => {
        if (!cancelled) setProviders(r.providers);
      })
      .catch(() => {
        if (!cancelled) setProviders([]);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const selectedSpec = providers?.find((p) => p.name === localProvider);
  const dirty = providerDirty || modelDirty;
  const unavailable = selectedSpec ? !selectedSpec.available : false;

  return (
    <label className="new-campaign-field">
      <span>Optimizer LLM</span>
      <span style={{ display: "flex", flexDirection: "column", gap: "0.4rem" }}>
        <span style={{ display: "flex", gap: "0.5rem" }}>
          <select
            value={localProvider}
            onChange={(e) => {
              setLocalProvider(e.target.value);
              setLocalModel("");
            }}
          >
            {(providers ?? [{ name: provider, display_name: provider, available: true, env_var: "", models: [], note: "" }]).map((p) => (
              <option key={p.name} value={p.name}>
                {p.display_name}
                {p.available ? "" : ` (no ${p.env_var})`}
              </option>
            ))}
          </select>
          <select
            value={localModel}
            onChange={(e) => setLocalModel(e.target.value)}
            style={{ flex: 1 }}
          >
            <option value="">— provider default —</option>
            {(selectedSpec?.models ?? []).map((m) => (
              <option key={m} value={m}>
                {m}
              </option>
            ))}
            {localModel && !(selectedSpec?.models ?? []).includes(localModel) ? (
              <option value={localModel}>{localModel} (custom)</option>
            ) : null}
          </select>
          <button
            type="button"
            disabled={!dirty}
            onClick={() => onApply(localProvider, localModel)}
          >
            Apply
          </button>
        </span>
        {unavailable && selectedSpec ? (
          <small className="new-campaign-error">
            Set <code>{selectedSpec.env_var}</code> in <code>.env</code> before applying — the runner will crash at first call otherwise.
          </small>
        ) : selectedSpec?.note ? (
          <small style={{ color: "var(--color-text-tertiary)" }}>{selectedSpec.note}</small>
        ) : null}
      </span>
    </label>
  );
}
