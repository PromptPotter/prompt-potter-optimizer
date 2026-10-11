import { describe, expect, it } from "vitest";
import { draftForCampaign } from "../draft-for-campaign";
import type { DraftCampaignWire } from "@/lib/api";

// Only `draft_id` is read, hence the cast.
const draft = (draft_id: string) => ({ draft_id }) as DraftCampaignWire;

describe("draftForCampaign", () => {
  it("hands back the draft that IS this campaign", () => {
    const d = draft("swiss__09daf6");
    expect(draftForCampaign(d, "swiss__09daf6")).toBe(d);
  });

  it("withholds a draft belonging to a DIFFERENT campaign", () => {
    expect(draftForCampaign(draft("swiss__09daf6"), "swiss__3ace04")).toBeNull();
  });

  it("is null when either side is missing", () => {
    expect(draftForCampaign(null, "swiss__3ace04")).toBeNull();
    expect(draftForCampaign(draft("swiss__09daf6"), null)).toBeNull();
    expect(draftForCampaign(null, null)).toBeNull();
  });
});
