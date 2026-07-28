"use client";
import { ForestRows, type TreeCtx } from "./sidebar/ForestRows";
import type { OriginGroup } from "./sidebar/grouping";

interface Props {
  origins: OriginGroup[];
  ctx: TreeCtx;
}

// The top-level forest — the tenant's own store. Pure renderer: the shell owns the
// workspace data, toggle state, and dataset filter that produce `origins`. Depth is NOT
// this tier's job: the recursion runs `CourseRow ⇄ CandidateRow`, so an L4 inner fan-out
// and an L5 descendant hang off the candidate they measured, and nothing re-enters here.
export function CampaignTreePane({ origins, ctx }: Props) {
  return (
    <ul className="unit-library-list">
      <ForestRows origins={origins} ctx={ctx} />
    </ul>
  );
}
