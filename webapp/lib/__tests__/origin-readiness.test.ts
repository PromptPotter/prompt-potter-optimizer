import { describe, expect, it } from "vitest";
import { questionOptions, questionPatch } from "../origin-readiness";

describe("questionPatch / questionOptions (resolver answer-back loop)", () => {
  it("maps each field id to its draft-patch key", () => {
    expect(questionPatch("column.query", "input")).toEqual({ column_query: "input" });
    expect(questionPatch("task_description", "map codes")).toEqual({
      raw_task_description: "map codes",
    });
    expect(questionPatch("max_rounds", "8")).toEqual({
      optimization_overrides: { max_rounds: 8 },
    });
  });

  it("rejects un-applicable answers so the caller skips them", () => {
    expect(questionPatch("max_rounds", "lots")).toBeNull();
    expect(questionPatch("max_rounds", "2.5")).toBeNull();
    expect(questionPatch("column.query", "  ")).toBeNull();
    expect(questionPatch("backend.node_config", "x")).toBeNull();
    expect(questionPatch("nonsense", "x")).toBeNull();
  });

  it("grounds a column question's options in the uploaded headers", () => {
    expect(questionOptions("column.query", [], ["a", "b"])).toEqual(["a", "b"]);
    expect(questionOptions("connector", ["termnorm"], ["a"])).toEqual(["termnorm"]);
    expect(questionOptions("task_description", [], ["a"])).toEqual([]);
  });
});
