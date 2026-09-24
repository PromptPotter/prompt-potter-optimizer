import { describe, expect, it } from "vitest";
import {
  applyFlatEdits,
  configRows,
  DESCRIPTION_PREFIX,
  descriptionSubtree,
  effortLadder,
  nodeLockPatch,
  nodeOverlayPatch,
  nodeSchemaPatch,
  overlayEdits,
  permittedModels,
  seedOverlayFromRows,
  type ConfigRow,
} from "../nodeConfig";
import type { ModelCapability, NodeConfigParam } from "@/lib/api";

function row(over: Partial<ConfigRow> & { key: string; kind: string }): ConfigRow {
  return {
    node: "n",
    options: [],
    value: "",
    baseValue: "",
    locked: false,
    allowed: [],
    stated: false,
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
  it("writes open params to param_keys and omits locked ones", () => {
    const patch = nodeOverlayPatch({}, "web_search", [
      row({ key: "max_sites", kind: "number" }),
      row({ key: "num_results", kind: "number", locked: true }),
    ]);
    const opt = (patch.pipeline_overlay!.web_search as Record<string, unknown>)
      .optimizer as Record<string, unknown>;
    expect(opt.param_keys).toEqual(["max_sites"]);
  });

  it("locks a node to [] when every param is locked", () => {
    const patch = nodeOverlayPatch({}, "fuzzy_matching", [
      row({ key: "threshold", kind: "number", locked: true }),
      row({ key: "scorer", kind: "enum", locked: true, options: ["WRatio", "QRatio"] }),
    ]);
    const opt = (patch.pipeline_overlay!.fuzzy_matching as Record<string, unknown>)
      .optimizer as Record<string, unknown>;
    expect(opt.param_keys).toEqual([]);
    expect(opt.param_allowed_values).toEqual({});
  });

  it("states an enum's permitted set whenever it DIFFERS from the menu, or the server stated it", () => {
    const patch = nodeOverlayPatch({}, "llm", [
      row({
        key: "reasoning_effort",
        kind: "enum",
        allowed: ["low", "medium"],
        options: ["low", "medium", "high"],
      }),
      row({ key: "scorer", kind: "enum", allowed: ["t", "j"], options: ["t", "j"] }),
      // Written out: an absent entry would resolve to the declaration and bounce `json` unticked.
      row({
        key: "response_format",
        kind: "enum",
        allowed: ["text", "json"],
        options: ["text", "json"],
        stated: true,
      }),
    ]);
    const opt = (patch.pipeline_overlay!.llm as Record<string, unknown>).optimizer as Record<
      string,
      string[]
    >;
    expect(opt.param_allowed_values).toEqual({
      reasoning_effort: ["low", "medium"],
      response_format: ["text", "json"],
    });
  });

  it("writes a WIDENED set too — an operator may add a value the node never declared", () => {
    const patch = nodeOverlayPatch({}, "llm", [
      row({
        key: "reasoning_effort",
        kind: "enum",
        allowed: ["low", "medium", "minimal"],
        options: ["low", "medium"],
      }),
    ]);
    const opt = (patch.pipeline_overlay!.llm as Record<string, unknown>).optimizer as Record<
      string,
      string[]
    >;
    expect(opt.param_allowed_values).toEqual({
      reasoning_effort: ["low", "medium", "minimal"],
    });
  });

  it("pins an enumerable axis narrowed to ONE value — no `locked` flag needed", () => {
    const patch = nodeOverlayPatch({}, "llm", [
      row({ key: "reasoning_effort", kind: "enum", allowed: ["low"], options: ["low", "high"] }),
      row({ key: "model", kind: "model", allowed: ["m1"], options: ["m1", "m2"] }),
    ]);
    const opt = (patch.pipeline_overlay!.llm as Record<string, unknown>).optimizer as Record<
      string,
      unknown
    >;
    expect(opt.param_keys).toEqual([]);
    // The permitted set is still stated — it is what a human fork may steer to un-tainted,
    // which outlives whether the optimizer may move the axis.
    expect(opt.param_allowed_values).toEqual({ reasoning_effort: ["low"], model: ["m1"] });
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
    expect((node.optimizer as Record<string, unknown>).param_keys).toEqual([
      "temperature",
      "model",
    ]);
  });

  it("merges onto an existing overlay without clobbering other nodes", () => {
    const base = { other_node: { config: { x: 1 } } };
    const patch = nodeOverlayPatch(base, "web_search", [
      row({ key: "max_sites", kind: "number" }),
    ]);
    expect(patch.pipeline_overlay!.other_node).toEqual({ config: { x: 1 } });
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
      movable_by: ["l1"],
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
      movable_by: ["l1"],
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
      movable_by: ["l1"],
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
      movable_by: ["l1"],
      held: false,
      source: "dataset",
      permitted: null,
    },
  ],
};

// Every field is served and the overlay argument is never read: a client-derived `locked` answers
// off whichever overlay the call site passed.
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
        param({ key: "temperature", value: 0.4, movable_by: ["l1"] }),
        param({ key: "max_tokens", value: 900 }),
      ],
    };
    // An overlay that contradicts every one of them. It must change nothing.
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
    // Narrowed: the menu keeps every value, so unticking stays reversible.
    expect(byKey.effort?.options).toEqual(["a", "b", "c"]);
    expect(byKey.effort?.allowed).toEqual(["a"]);
    // Un-narrowed: `null` is NOT `[]`, and reading it as "nothing permitted" would empty the axis.
    expect(byKey.fmt?.allowed).toEqual(["x", "y"]);
  });

  it("value and baseValue start equal, so an untouched row emits nothing", () => {
    const rows = configRows({ n: [param({ key: "temperature", value: 0.4 })] }, {}, "search-space");
    const patch = nodeOverlayPatch({}, "n", rows);
    expect((patch.pipeline_overlay!.n as Record<string, unknown>).config).toBeUndefined();
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
    expect(byKey.model!.neverAxis).toBe(""); // an axis, not a cost lever
    expect(byKey.reasoning_effort!.kind).toBe("enum");
    expect(byKey.temperature!.kind).toBe("number");
    expect(byKey.max_tokens!.value).toBe(""); // declared but unset
  });

  it("seeds the value from the candidate overlay over the config floor", () => {
    const rows = configRows(schema, { llm_only: { reasoning_effort: "high" } }, "values");
    const re = rows.find((r) => r.key === "reasoning_effort")!;
    expect(re.value).toBe("high");
    expect(re.inSeed).toBe(true);
    expect(rows.find((r) => r.key === "temperature")!.inSeed).toBe(false);
    // Carried by the seed is not provenance: the badge reads what the server stamped.
    expect(re.source).toBe("dataset");
  });

  it("returns no rows without a schema", () => {
    expect(configRows(null, {}, "values")).toEqual([]);
  });

  // A structured param is an AXIS, drawn as JSON text: an edit emits the parsed OBJECT, never the
  // text, and a draft that does not parse yet emits nothing.
  it("round-trips a nested param through JSON, and emits the OBJECT", () => {
    const nested: NodeConfigParam = {
      key: "layout",
      value: { instruction: ["plan"] },
      kind: "nested",
      options: [],
      description: "",
      never_axis: "",
      movable_by: ["l1"],
      held: false,
      source: "dataset",
      permitted: null,
    };
    const withNested = { llm_only: [...schema.llm_only!, nested] };
    const rows = configRows(withNested, {}, "values");
    const drawn = rows.find((r) => r.key === "layout")!;
    expect(drawn.movableBy).toEqual(["l1"]);
    expect(JSON.parse(drawn.value)).toEqual({ instruction: ["plan"] });
    expect(seedOverlayFromRows(rows, {})).toEqual({});
    const edited = seedOverlayFromRows(rows, { "llm_only.layout": '{"instruction":["critique"]}' });
    expect(edited.llm_only!.layout).toEqual({ instruction: ["critique"] });

    expect(seedOverlayFromRows(rows, { "llm_only.layout": '{"instruction": ' })).toEqual({});
  });

  // `schema_owned` fences only the OPTIMIZER (`node_param_keys` / `l1_strict` /
  // `build_l1_response_schema`), never the operator setting the value.
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
    // Still not an AXIS: `movable_by: []` locks the row, and `nodeNarrowing` builds `param_keys`
    // from unlocked rows only.
    const optimizer = (patch.pipeline_overlay!.llm_only as { optimizer: { param_keys: string[] } })
      .optimizer;
    expect(optimizer.param_keys).not.toContain("answer_field");
  });

  // A prompt field's lock is a `param_keys` membership like any param's, so every emit must carry
  // it — a row left out of the emission reads as the operator holding it.
  it("carries a prompt field's lock in every emit, and keeps its text out of a fork seed", () => {
    const prompt = (key: string, open: boolean): NodeConfigParam => ({
      key,
      value: "text",
      kind: "prompt",
      options: [],
      description: "",
      never_axis: "",
      movable_by: open ? ["l1"] : [],
      held: !open,
      source: "dataset",
      permitted: null,
    });
    const withPrompt = {
      llm_only: [...schema.llm_only!, prompt("instruction", true), prompt("persona", false)],
    };
    const keysOf = (p: ReturnType<typeof nodeOverlayPatch>) =>
      (p.pipeline_overlay!.llm_only as { optimizer: { param_keys: string[] } }).optimizer
        .param_keys;
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

// `values` mode emits the WHOLE running config, so read raw it marks every parameter edited at
// once; these two recover what the operator actually changed.
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
    // The editor drops its own draft when the seed changes, so the scenario has to survive the
    // round trip — otherwise a second keystroke would clear the first.
    const edits = new Map([["llm_only.temperature", "0.7"]]);
    const reseeded = applyFlatEdits(seed, edits);
    const emitted = seedOverlayFromRows(configRows(schema, reseeded, "values"), {});
    expect(overlayEdits(emitted, seed)).toEqual({ "llm_only.temperature": "0.7" });
  });

  it("leaves the seed alone with nothing edited", () => {
    expect(applyFlatEdits(seed, new Map())).toBe(seed);
  });
});

// The taint verdict is served (`POST /campaigns/{id}/fork-preview`); this decides only what the
// babysit warning lists.
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

// The ladder joins the MENU, never the ticks: the engine's intersection is `param_options`
// (`tests/test_numerics.py`), and folding it in would emit a model's refusals as narrowing.
describe("effortLadder", () => {
  const caps = (over: Partial<ModelCapability>): ModelCapability => ({
    model: "m",
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

describe("nodeSchemaPatch (an authored output contract)", () => {
  const props = (...names: string[]) =>
    Object.fromEntries(names.map((n) => [n, { type: "string" }]));

  it("answers under a first schema in one patch, re-opening the toggle a prior narrowing shut", () => {
    const patch = nodeSchemaPatch(
      {
        llm_only: {
          config: { model: "m", response_format: "text" },
          optimizer: {
            param_keys: ["temperature"],
            param_allowed_values: { model: ["m"], response_format: ["text"] },
          },
        },
      },
      "llm_only",
      { type: "object", properties: props("reasoning", "code") },
    );
    const block = patch.pipeline_overlay!.llm_only as Record<string, Record<string, unknown>>;
    expect(block.config).toMatchObject({
      model: "m",
      response_format: "json",
      answer_field: "code",
    });
    expect(block.optimizer).toEqual({
      param_keys: ["temperature", "response_format"],
      param_allowed_values: { model: ["m"] },
    });
  });

  it("keeps the chosen answer slot while it exists, takes a picked one, and leaves the toggle on a re-edit", () => {
    const base = {
      n: {
        config: { output_schema: { properties: props("code") }, answer_field: "code", response_format: "text" },
      },
    };
    const cfg = (p: ReturnType<typeof nodeSchemaPatch>) =>
      (p.pipeline_overlay!.n as Record<string, Record<string, unknown>>).config;
    expect(cfg(nodeSchemaPatch(base, "n", { properties: props("answer", "code") }))).toMatchObject({
      answer_field: "code",
      response_format: "text",
    });
    expect(cfg(nodeSchemaPatch(base, "n", { properties: props("answer", "notes") }))).toMatchObject({
      answer_field: "answer",
    });
    expect(
      cfg(nodeSchemaPatch(base, "n", { properties: props("answer", "notes") }, "notes")),
    ).toMatchObject({ answer_field: "notes" });
  });
});

describe("descriptionSubtree (one click on a schema-tree row)", () => {
  const keys = ["lines", "lines.amount", "lines.amount_net", "lines.tax.rate", "total"].map(
    (p) => DESCRIPTION_PREFIX + p,
  );

  // The lock's inheritance is this gesture alone: a field and everything beneath it, never a
  // sibling whose name merely starts the same way.
  it("takes a field and every key beneath it, the whole schema at the head", () => {
    expect(descriptionSubtree(keys, "lines.amount")).toEqual([DESCRIPTION_PREFIX + "lines.amount"]);
    expect(descriptionSubtree(keys, "lines")).toEqual(keys.slice(0, 4));
    expect(descriptionSubtree(keys, "")).toEqual(keys);
  });
});

