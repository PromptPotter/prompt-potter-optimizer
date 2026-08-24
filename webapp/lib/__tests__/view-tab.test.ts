import { describe, expect, it } from "vitest";
import {
  DEFAULT_TAB,
  PRIMARY_TABS,
  RECORDS_ENTRY,
  RECORDS_TABS,
  groupOf,
  isRecordsTab,
  isTab,
  type Tab,
} from "../view-tab";

const ALL: readonly Tab[] = [...PRIMARY_TABS, ...RECORDS_TABS];

describe("view-tab", () => {
  it("groups every view — the strip's top row has exactly three values", () => {
    expect(ALL.map(groupOf)).toEqual(["chat", "dashboard", "records", "records", "records"]);
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
