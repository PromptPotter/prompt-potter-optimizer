"use client";
import { ForestRows, type TreeCtx } from "./sidebar/ForestRows";
import type { OriginGroup } from "@/lib/derivations";

interface Props {
  origins: OriginGroup[];
  ctx: TreeCtx;
}

// The top-level forest. Depth is NOT this tier's job: the recursion runs
// `CourseRow ⇄ CandidateRow`, and nothing re-enters here.
export function CampaignTreePane({ origins, ctx }: Props) {
  return (
    <ul className="unit-library-list">
      <ForestRows origins={origins} ctx={ctx} />
    </ul>
  );
}
