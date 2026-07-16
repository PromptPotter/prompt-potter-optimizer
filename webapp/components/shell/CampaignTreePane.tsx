"use client";
import { ForestRows, type TreeCtx } from "./sidebar/ForestRows";
import type { OriginGroup } from "./sidebar/grouping";

interface Props {
  origins: OriginGroup[];
  ctx: TreeCtx;
}

// The top-level forest — `at: []`, the tenant's own store. Pure renderer: the
// shell owns the workspace data, toggle state, and dataset filter that produce
// `origins`. Everything below is ForestRows recursing, so an L4 cycle's inner
// fan-out and an L5 descendant render through the identical path with a longer
// `at` — there is no second tree.
export function CampaignTreePane({ origins, ctx }: Props) {
  return (
    <ul className="unit-library-list">
      <ForestRows origins={origins} at={[]} ctx={ctx} />
    </ul>
  );
}
