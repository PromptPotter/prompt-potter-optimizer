import { describe, expect, it } from "vitest";
import {
  applyFlatEdits,
  configRows,
  descriptionSubtree,
  effortLadder,
  knobValue,
  nodeLockPatch,
  parseKnob,
  nodeOverlayPatch,
  overlayEdits,
  permittedModels,
  seedOverlayFromRows,
  type ConfigRow,
} from "../nodeConfig";
import type { KnobRow, ModelCapability, NodeConfigParam } from "@/lib/api";
import { SCHEMA_DESCRIPTION_PREFIX as DESCRIPTION_PREFIX } from "@/lib/api/types.generated";

function row(over: Partial<ConfigRow> & { key: string; kind: string }): ConfigRow {
  return {
    node: "n",
    options: [],
    value: "",
    baseValue: "",
    locked: false,
    allowed: [],
    inSeed: false,
    source: "unset",
    neverAxis: "",
    movableBy: [],
    held: false,
    description: "",
    ...over,
  };
}

describe("nodeOverlayPatch (search-space emit)", () => {
  it("sends each row as drawn — its padlock and its ticks — and decides nothing about them", () => {
    const patch = nodeOverlayPatch({}, "web_search", [
      row({ key: "max_sites", kind: "number" }),
      row({ key: "num_results", kind: "number", locked: true }),
      row({ key: "scorer", kind: "enum", allowed: ["t"], options: ["t", "j"] }),
    ]);
    expect(patch.node_narrowing).toEqual({
      web_search: [
        { key: "max_sites", open: true, allowed: [] },
        { key: "num_results", open: false, allowed: [] },
        { key: "scorer", open: true, allowed: ["t"] },
      ],
    });
    expect(patch.pipeline_overlay).toBeUndefined();
  });

  it("carries a changed origin value into config; model is an axis like any other", () => {
    const patch = nodeOverlayPatch({}, "entity_profiling", [
      row({ key: "temperature", kind: "number", value: "0.2", baseValue: "0.3" }),
      row({
        key: "model",
        kind: "model",
        value: "m2",
        baseValue: "m1",
        allowed: ["m1", "m2"],
        options: ["m1", "m2"],
      }),
    ]);
    const node = patch.pipeline_overlay!.entity_profiling as Record<string, unknown>;
    expect(node.config).toEqual({ temperature: 0.2, model: "m2" });
    expect(patch.node_narrowing!.entity_profiling!.map((r) => r.key)).toEqual([
      "temperature",
      "model",
    ]);
  });

  it("merges onto an existing overlay without clobbering other nodes or the node's own block", () => {
    const kept = { param_keys: ["max_sites"], param_allowed_values: {} };
    const base = {
      other_node: { config: { x: 1 } },
      web_search: { optimizer: kept, config: { depth: 2 } },
    };
    const patch = nodeOverlayPatch(base, "web_search", [
      row({ key: "max_sites", kind: "number", value: "3", baseValue: "5" }),
    ]);
    expect(patch.pipeline_overlay!.other_node).toEqual({ config: { x: 1 } });
    expect(patch.pipeline_overlay!.web_search).toEqual({
      optimizer: kept,
      config: { depth: 2, max_sites: 3 },
    });
  });
});

const schema: Record<string, NodeConfigParam[]> = {
  llm_only: [
    {
      key: "model",
      value: "openai/gpt-oss-120b",
      kind: "model",
      options: ["openai/gpt-oss-120b", "openai/gpt-oss-20b"],
      description: "",
      never_axis: "",
      movable_by: ["proposer"],
      held: false,
      source: "dataset",
      permitted: null,
    },
    {
      key: "reasoning_effort",
      value: "low",
      kind: "enum",
      options: ["low", "medium", "high"],
      description: "",
      never_axis: "",
      movable_by: ["proposer"],
      held: false,
      source: "dataset",
      permitted: null,
    },
    {
      key: "temperature",
      value: 0,
      kind: "number",
      options: [],
      description: "",
      never_axis: "",
      movable_by: ["proposer"],
      held: false,
      source: "dataset",
      permitted: null,
    },
    {
      key: "max_tokens",
      value: null,
      kind: "number",
      options: [],
      description: "",
      never_axis: "",
      movable_by: ["proposer"],
      held: false,
      source: "dataset",
      permitted: null,
    },
  ],
};

describe("configRows (search-space mode)", () => {
  const param = (over: Partial<NodeConfigParam> & { key: string }): NodeConfigParam => ({
    value: null,
    kind: "number",
    options: [],
    description: "",
    never_axis: "",
    movable_by: [],
    held: false,
    source: "campaign",
    permitted: null,
    ...over,
  });

  it("takes lock, value and permitted set from the SERVED row, ignoring the overlay", () => {
    const served = {
      n: [
        param({ key: "temperature", value: 0.4, movable_by: ["proposer"] }),
        param({ key: "max_tokens", value: 900 }),
      ],
    };
    const lie = { n: { config: { temperature: 9 }, optimizer: { param_keys: ["max_tokens"] } } };
    const rows = configRows(served, lie, "search-space", "n");
    const byKey = Object.fromEntries(rows.map((r) => [r.key, r]));
    expect(byKey.temperature?.value).toBe("0.4");
    expect(byKey.temperature?.locked).toBe(false);
    expect(byKey.max_tokens?.locked).toBe(true);
  });

  it("a row's menu is `options` and its ticks are `permitted` — null means they are the same", () => {
    const rows = configRows(
      {
        n: [
          param({ key: "effort", kind: "enum", options: ["a", "b", "c"], permitted: ["a"] }),
          param({ key: "fmt", kind: "enum", options: ["x", "y"] }),
        ],
      },
      {},
      "search-space",
      "n",
    );
    const byKey = Object.fromEntries(rows.map((r) => [r.key, r]));
    expect(byKey.effort?.options).toEqual(["a", "b", "c"]);
    expect(byKey.effort?.allowed).toEqual(["a"]);
    // Un-narrowed: `null` is NOT `[]`, and reading it as "nothing permitted" would empty the axis.
    expect(byKey.fmt?.allowed).toEqual(["x", "y"]);
  });

  it("value and baseValue start equal, so an untouched row emits nothing", () => {
    const rows = configRows({ n: [param({ key: "temperature", value: 0.4 })] }, {}, "search-space");
    expect(nodeOverlayPatch({}, "n", rows).pipeline_overlay).toBeUndefined();
  });
});

describe("configRows (values mode)", () => {
  it("exposes the full config surface — model included with its options", () => {
    const rows = configRows(schema, {}, "values");
    const byKey = Object.fromEntries(rows.map((r) => [r.key, r]));
    expect(rows.map((r) => r.key).sort()).toEqual([
      "max_tokens",
      "model",
      "reasoning_effort",
      "temperature",
    ]);
    expect(byKey.model!.kind).toBe("model");
    expect(byKey.model!.options).toEqual(["openai/gpt-oss-120b", "openai/gpt-oss-20b"]);
    expect(byKey.model!.neverAxis).toBe("");
    expect(byKey.reasoning_effort!.kind).toBe("enum");
    expect(byKey.temperature!.kind).toBe("number");
    expect(byKey.max_tokens!.value).toBe("");
  });

  it("seeds the value from the candidate overlay over the config floor", () => {
    const rows = configRows(schema, { llm_only: { reasoning_effort: "high" } }, "values");
    const re = rows.find((r) => r.key === "reasoning_effort")!;
    expect(re.value).toBe("high");
    expect(re.inSeed).toBe(true);
    expect(rows.find((r) => r.key === "temperature")!.inSeed).toBe(false);
    expect(re.source).toBe("dataset");
  });

  it("returns no rows without a schema", () => {
    expect(configRows(null, {}, "values")).toEqual([]);
  });

  it("round-trips a nested param through JSON, and emits the OBJECT", () => {
    const nested: NodeConfigParam = {
      key: "layout",
      value: { instruction: ["plan"] },
      kind: "nested",
      options: [],
      description: "",
      never_axis: "",
      movable_by: ["proposer"],
      held: false,
      source: "dataset",
      permitted: null,
    };
    const withNested = { llm_only: [...schema.llm_only!, nested] };
    const rows = configRows(withNested, {}, "values");
    const drawn = rows.find((r) => r.key === "layout")!;
    expect(drawn.movableBy).toEqual(["proposer"]);
    expect(JSON.parse(drawn.value)).toEqual({ instruction: ["plan"] });
    expect(seedOverlayFromRows(rows, {})).toEqual({});
    const edited = seedOverlayFromRows(rows, { "llm_only.layout": '{"instruction":["critique"]}' });
    expect(edited.llm_only!.layout).toEqual({ instruction: ["critique"] });

    expect(seedOverlayFromRows(rows, { "llm_only.layout": '{"instruction": ' })).toEqual({});
  });

  // `schema_owned` fences only the OPTIMIZER, never the operator setting the value.
  it("lets the operator set a schema-owned key, and never makes it an axis", () => {
    const owned: NodeConfigParam = {
      key: "answer_field",
      value: "answer",
      kind: "string",
      options: [],
      description: "",
      never_axis: "schema_owned",
      movable_by: [],
      held: false,
      source: "dataset",
      permitted: null,
    };
    const withOwned = { llm_only: [...schema.llm_only!, owned] };
    const rows = configRows(withOwned, { llm_only: { answer_field: "answer" } }, "values");
    expect(rows.find((r) => r.key === "answer_field")!.value).toBe("answer");
    expect(seedOverlayFromRows(rows, { "llm_only.answer_field": "reasoning" })).toEqual({
      llm_only: { answer_field: "reasoning" },
    });

    const patch = nodeOverlayPatch(
      {},
      "llm_only",
      configRows(withOwned, {}, "search-space", "llm_only").map((r) =>
        r.key === "answer_field" ? { ...r, value: "reasoning" } : r,
      ),
    );
    expect(patch.pipeline_overlay!.llm_only).toHaveProperty("config", {
      answer_field: "reasoning",
    });
    expect(patch.node_narrowing!.llm_only!.find((r) => r.key === "answer_field")!.open).toBe(false);
  });

  // A row left out of the emission reads as the operator holding it.
  it("carries a prompt field's lock in every emit, and keeps its text out of a fork seed", () => {
    const prompt = (key: string, open: boolean): NodeConfigParam => ({
      key,
      value: "text",
      kind: "prompt",
      options: [],
      description: "",
      never_axis: "",
      movable_by: open ? ["proposer"] : [],
      held: !open,
      source: "dataset",
      permitted: null,
    });
    const withPrompt = {
      llm_only: [...schema.llm_only!, prompt("instruction", true), prompt("persona", false)],
    };
    const keysOf = (p: ReturnType<typeof nodeOverlayPatch>) =>
      p.node_narrowing!.llm_only!.filter((r) => r.open).map((r) => r.key);
    const emitted = keysOf(
      nodeOverlayPatch({}, "llm_only", configRows(withPrompt, {}, "search-space", "llm_only")),
    );
    expect(emitted).toContain("instruction");
    expect(emitted).not.toContain("persona");
    expect(keysOf(nodeLockPatch(withPrompt, {}, "llm_only", ["instruction"], true))).not.toContain(
      "instruction",
    );
    const seed = configRows(withPrompt, { llm_only: { instruction: "evolved" } }, "values");
    expect(seedOverlayFromRows(seed, {})).toEqual({});
  });

  it("scopes rows to one node when `node` is given (OBSERVE drill-in vs whole-pipeline)", () => {
    const multi = { web_search: schema.llm_only!, llm_only: schema.llm_only! };
    expect(new Set(configRows(multi, {}, "values", "llm_only").map((r) => r.node))).toEqual(
      new Set(["llm_only"]),
    );
    expect(new Set(configRows(multi, {}, "values").map((r) => r.node))).toEqual(
      new Set(["web_search", "llm_only"]),
    );
  });
});

describe("seedOverlayFromRows (values emit)", () => {
  const rows = configRows(schema, { llm_only: { reasoning_effort: "high" } }, "values");

  it("keeps candidate params + operator edits, drops inherited-untouched", () => {
    const overlay = seedOverlayFromRows(rows, { "llm_only.temperature": "0.7" });
    expect(overlay).toEqual({ llm_only: { reasoning_effort: "high", temperature: 0.7 } });
  });

  it("an untouched dialog re-emits exactly the candidate overlay", () => {
    expect(seedOverlayFromRows(rows, {})).toEqual({ llm_only: { reasoning_effort: "high" } });
  });

  it("lets the operator override the model on a fork", () => {
    const overlay = seedOverlayFromRows(rows, { "llm_only.model": "openai/gpt-oss-20b" });
    expect(overlay.llm_only!.model).toBe("openai/gpt-oss-20b");
  });
});

describe("overlayEdits + applyFlatEdits", () => {
  const seed = { steps: ["llm_only"], llm_only: { reasoning_effort: "high", temperature: 0.2 } };

  it("an untouched emission is no edit at all", () => {
    const rows = configRows(schema, seed, "values");
    expect(overlayEdits(seedOverlayFromRows(rows, {}), seed)).toEqual({});
  });

  it("names only what moved", () => {
    const rows = configRows(schema, seed, "values");
    const emitted = seedOverlayFromRows(rows, { "llm_only.temperature": "0.7" });
    expect(overlayEdits(emitted, seed)).toEqual({ "llm_only.temperature": "0.7" });
  });

  it("a param the seed carried and the emission dropped reads as cleared", () => {
    expect(overlayEdits({ llm_only: { reasoning_effort: "high" } }, seed)).toEqual({
      "llm_only.temperature": "",
    });
  });

  it("re-seeding with an edit makes the emission idempotent", () => {
    // The editor drops its draft when the seed changes, so a second keystroke must not clear the first.
    const edits = new Map([["llm_only.temperature", "0.7"]]);
    const reseeded = applyFlatEdits(seed, edits);
    const emitted = seedOverlayFromRows(configRows(schema, reseeded, "values"), {});
    expect(overlayEdits(emitted, seed)).toEqual({ "llm_only.temperature": "0.7" });
  });

  it("leaves the seed alone with nothing edited", () => {
    expect(applyFlatEdits(seed, new Map())).toBe(seed);
  });
});

describe("permittedModels", () => {
  const modelRow = (over: Partial<NodeConfigParam>): NodeConfigParam[] => [
    {
      key: "model",
      value: null,
      kind: "model",
      options: ["a", "b"],
      description: "",
      never_axis: "",
      movable_by: [],
      held: false,
      source: "campaign",
      permitted: null,
      ...over,
    },
  ];

  it("takes the served permitted set where the gate is narrower than the menu", () => {
    expect(permittedModels({ l1_generate: modelRow({ permitted: ["a"] }) })).toEqual({
      l1_generate: ["a"],
    });
  });

  it("falls to `options` on null — which is the menu BEING the permitted set", () => {
    expect(permittedModels({ l1_generate: modelRow({}) })).toEqual({ l1_generate: ["a", "b"] });
  });

  it("keeps an EMPTY permitted set empty — nothing may be picked is not the same as null", () => {
    expect(permittedModels({ l1_generate: modelRow({ permitted: [] }) })).toEqual({
      l1_generate: [],
    });
  });

  it("names only nodes that carry a model row at all", () => {
    expect(permittedModels({ scorer: [] })).toEqual({});
    expect(permittedModels(null)).toEqual({});
    expect(permittedModels(undefined)).toEqual({});
  });
});

describe("effortLadder", () => {
  const caps = (over: Partial<ModelCapability>): ModelCapability => ({
    model: "m",
    provider: "",
    reasoning_efforts: null,
    reasoning_note: "",
    unsupported_params: null,
    indistinct_efforts: null,
    source: "unknown",
    display_name: "",
    context_length: null,
    max_output_tokens: null,
    input_usd_per_mtok: null,
    output_usd_per_mtok: null,
    modality: "",
    moderated: null,
    fetched_at: "",
    ...over,
  });
  const effort = row({
    key: "reasoning_effort",
    kind: "enum",
    options: ["low", "medium"],
  });

  it("replaces the node's list — WIDER is the point, not only narrower", () => {
    expect(
      effortLadder(effort, caps({ reasoning_efforts: ["none", "low", "medium", "high"] })),
    ).toEqual(["none", "low", "medium", "high"]);
  });

  it("falls back to the node's list when the model is UNKNOWN", () => {
    expect(effortLadder(effort, caps({}))).toEqual(["low", "medium"]);
    expect(effortLadder(effort, undefined)).toEqual(["low", "medium"]);
  });

  it("leaves every other axis alone", () => {
    const model = row({ key: "model", kind: "model", options: ["a", "b"] });
    expect(effortLadder(model, caps({ reasoning_efforts: ["high"] }))).toEqual(["a", "b"]);
  });
});

describe("descriptionSubtree (one click on a schema-tree row)", () => {
  const keys = ["lines", "lines.amount", "lines.amount_net", "lines.tax.rate", "total"].map(
    (p) => DESCRIPTION_PREFIX + p,
  );

  it("takes a field and every key beneath it, the whole schema at the head", () => {
    expect(descriptionSubtree(keys, "lines.amount")).toEqual([DESCRIPTION_PREFIX + "lines.amount"]);
    expect(descriptionSubtree(keys, "lines")).toEqual(keys.slice(0, 4));
    expect(descriptionSubtree(keys, "")).toEqual(keys);
  });
});

describe("parseKnob / knobValue (an optimizer knob typed in on the check-in)", () => {
  const knob = (over: Partial<KnobRow>): KnobRow => ({
    key: "k",
    description: "",
    type: "integer",
    options: null,
    nullable: false,
    minimum: null,
    exclusive_minimum: null,
    maximum: null,
    exclusive_maximum: null,
    value: 10,
    ...over,
  });

  it("commits only what the served type reads as the typed value", () => {
    expect(parseKnob(knob({}), "4")).toBe(4);
    expect(parseKnob(knob({}), "1.5")).toBeUndefined();
    expect(parseKnob(knob({}), "")).toBeUndefined();
    expect(parseKnob(knob({ type: "number" }), "0.05")).toBe(0.05);
    expect(parseKnob(knob({ type: "number", nullable: true }), " ")).toBeNull();
    expect(parseKnob(knob({ type: "array" }), "[0.3, 1.2]")).toEqual([0.3, 1.2]);
    expect(parseKnob(knob({ type: "array" }), "[0.3,")).toBeUndefined();
    const epsilon = knob({ type: "number", exclusive_minimum: 0, exclusive_maximum: 1 });
    expect(parseKnob(epsilon, "0")).toBeUndefined();
    expect(parseKnob(epsilon, "0.3")).toBe(0.3);
    expect(parseKnob(knob({ minimum: 1 }), "1")).toBe(1);
  });

  it("reads the draft's value where it sets the key, the manifest's where it does not", () => {
    const nodes = { paired_t: { config: { alpha: 0.1 } } };
    expect(knobValue(nodes, "paired_t", knob({ key: "alpha", value: 0.2 }))).toBe(0.1);
    expect(knobValue(nodes, "paired_t", knob({ key: "survivors", value: 10 }))).toBe(10);
    expect(knobValue(null, "blocks", knob({ key: "block_size", value: 30 }))).toBe(30);
  });
});

