"use client";
// The campaign's model allow-list — which inner-optimizer models a human may steer a
// fork to WITHOUT tainting it (grade C). The optimizer never touches this; it governs
// only the direct human steer. Read-only unless the operator holds `campaign.lifecycle`
// (owner) — editing posts `set-allowed-models`, which rewrites the single source of
// truth (frozen `campaign.json::config`, read by both the fork cap-gate and the runner
// stamp). Empty = nothing sanctioned = restrictive default (any model steer taints).

import { useMemo, useState } from "react";
import { fetchCampaignDetail, postSetAllowedModels } from "@/lib/api";
import { useFetch } from "@/lib/hooks/useFetch";
import { useConnector } from "@/lib/hooks/useConnector";
import { useWorkspace } from "@/lib/workspace";
import { useAuth } from "@/lib/auth-context";
import { bumpRevalidation } from "@/lib/revalidate";
import { Chip, ChipGroup } from "@/components/ui";

export function AllowedModelsPanel() {
  const { campaignId } = useWorkspace();
  const cv = useConnector();
  const { me } = useAuth();
  const canEdit = !!me?.capabilities?.includes("campaign.lifecycle");
  const { data: detail } = useFetch(
    campaignId ? (signal) => fetchCampaignDetail(campaignId, signal) : null,
    [campaignId],
  );
  const served = (detail?.config.allowed_models as string[] | undefined) ?? [];

  // The model catalogue — union of every model-kind param's options across nodes.
  const catalogue = useMemo(() => {
    const set = new Set<string>();
    for (const params of Object.values(cv.nodeConfigSchema ?? {})) {
      for (const p of params) if (p.kind === "model") for (const o of p.options) set.add(o);
    }
    return [...set];
  }, [cv.nodeConfigSchema]);

  // Optimistic copy so a click reflects before the refetch lands. Render-phase reset
  // when the viewed campaign switches (webapp/CLAUDE.md § State reset on prop change).
  const [local, setLocal] = useState<string[] | null>(null);
  const [prevCampaign, setPrevCampaign] = useState(campaignId);
  const [err, setErr] = useState<string | null>(null);
  if (campaignId !== prevCampaign) {
    setPrevCampaign(campaignId);
    setLocal(null);
    setErr(null);
  }
  const allowed = local ?? served;

  if (!campaignId) return <p className="mech-empty">Select a campaign to see its allow-list.</p>;
  if (catalogue.length === 0) {
    return <p className="mech-empty">This pipeline exposes no steerable model.</p>;
  }

  const toggle = async (model: string) => {
    if (!canEdit) return;
    const next = allowed.includes(model)
      ? allowed.filter((m) => m !== model)
      : [...allowed, model];
    const prev = allowed;
    setLocal(next);
    setErr(null);
    try {
      await postSetAllowedModels(campaignId, next);
      bumpRevalidation();
    } catch (e) {
      setLocal(prev); // revert the optimistic flip
      setErr((e as Error).message);
    }
  };

  return (
    <div className="allowed-models-panel">
      <p className="mech-empty">
        Which models a human may steer a fork to without tainting it (grade C). The
        optimizer never changes this.
        {canEdit ? "" : " Read-only — needs the owner (campaign.lifecycle) capability."}
      </p>
      <ChipGroup label="Allowed models" showLabel>
        {catalogue.map((m) => (
          <Chip
            key={m}
            on={allowed.includes(m)}
            disabled={!canEdit}
            onClick={() => void toggle(m)}
            title={`${allowed.includes(m) ? "Allowed" : "Not allowed"}: ${m}`}
          >
            {m}
          </Chip>
        ))}
      </ChipGroup>
      {err && (
        <span className="steer-fork-err" role="alert">
          allow-list: {err}
        </span>
      )}
    </div>
  );
}
