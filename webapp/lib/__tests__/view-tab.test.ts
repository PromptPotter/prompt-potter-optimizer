import { describe, expect, it } from "vitest";
import {
  DEFAULT_TAB,
  PRIMARY_TABS,
  RECORDS_ENTRY,
  RECORDS_TABS,
  WORKSPACE_TABS,
  groupOf,
  isRecordsTab,
  isTab,
  isWorkspaceTab,
  type CampaignTab,
  type Tab,
} from "../view-tab";

const CAMPAIGN: readonly CampaignTab[] = [...PRIMARY_TABS, ...RECORDS_TABS];
const ALL: readonly Tab[] = [...CAMPAIGN, ...WORKSPACE_TABS];

describe("view-tab", () => {
  it("groups every campaign view — the strip has exactly three values", () => {
    expect(CAMPAIGN.map(groupOf)).toEqual(["chat", "dashboard", "records", "records"]);
  });

  it("a workspace view is on no campaign strip — it reads across campaigns", () => {
    for (const t of WORKSPACE_TABS) {
      expect(isWorkspaceTab(t)).toBe(true);
      expect(isRecordsTab(t)).toBe(false);
    }
    for (const t of CAMPAIGN) expect(isWorkspaceTab(t)).toBe(false);
  });

  it("the Records entry is inside Records, so arriving there lights its own segment", () => {
    expect(isRecordsTab(RECORDS_ENTRY)).toBe(true);
    expect(groupOf(RECORDS_ENTRY)).toBe("records");
  });

  it("the default view is a primary one — the address omits it", () => {
    expect(isRecordsTab(DEFAULT_TAB)).toBe(false);
  });

  it("every view is an address word, and nothing else is", () => {
    for (const t of ALL) expect(isTab(t)).toBe(true);
    // The group is a strip value, NOT a view: an address naming it must not parse.
    expect(isTab("records")).toBe(false);
  });
});
