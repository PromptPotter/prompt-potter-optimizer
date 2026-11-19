# addr-4 review probes (ran on git-archive copies; adapt paths)

## test_probe_identity_drift.py
```
"""Probe: does a moved cell identity (judge / backend fingerprint) still refuse a resume?"""
import pytest
from promptpotter.application import pipeline_resolve
from promptpotter.application.jobs.mint import ConfigDriftError, resolve_resume
from tests.test_identity import decl, held  # noqa: F401  (fixtures)


def test_probe_moved_identity_refuses_resume(held, decl, monkeypatch) -> None:
    opened, held_campaign = held
    rows_30 = decl.rows_30
    real = pipeline_resolve.identity_layer

    def moved(*a, **k):
        out = real(*a, **k)
        out.setdefault("solve", {})["judge_instrument"] = "a-moved-instrument"
        return out

    monkeypatch.setattr(pipeline_resolve, "identity_layer", moved)
    try:
        plan = resolve_resume(opened, held_campaign, rows_30)
        print("\nRESUME PASSED under a moved identity; plan.session.instrument_id =",
              plan.session.instrument_id, "cell_identity:", getattr(plan.session, "cell_identity", None))
        outcome = "passed"
    except ConfigDriftError as exc:
        print("\nRESUME REFUSED:", str(exc)[:160])
        outcome = "refused"
    print("OUTCOME", outcome)
```

## p1_null.py
```
import sys; sys.path.insert(0, ".")
from promptpotter.application.datasets.authored import ConfigLayers, campaign_defaults, declare_run, effective_config, selected_config
from promptpotter.application.datasets import authored
import inspect
for knob in ({"transcripts": None}, {"transcripts": "unbounded"}):
    raw = {"optimization": {"nodes": {"l1_critique": {"config": knob}}}}
    layers = ConfigLayers(campaign_defaults(), raw)
    try:
        cfg = layers.config()
        print("RAW", knob, "-> config nodes:", {n: dict(o.config) for n, o in cfg.optimization.nodes.items()})
        spec = declare_run(cfg, dataset_hash="x", connector="llm_only", bench_defaults_pin="p")
        eff = effective_config(spec)
        print("   loop.nodes:", spec.loop.get("optimization", {}).get("nodes"))
        print("   eff nodes:", {n: dict(o.config) for n, o in eff.optimization.nodes.items()}, "ROUNDTRIP", eff == cfg)
    except Exception as e:
        print("RAW", knob, "->", type(e).__name__, str(e)[:200])
# budget null round trip
raw = {"optimization": {"budget": {"rounds": None}}}
cfg = ConfigLayers(campaign_defaults(), raw).config()
print("BUDGET raw", cfg.optimization.budget, cfg.optimization.budget.named)
spec = declare_run(cfg, dataset_hash="x", connector="llm_only", bench_defaults_pin="p")
eff = effective_config(spec)
print("  eff budget", eff.optimization.budget, "rounds", eff.optimization.budget.rounds)
# door patch: over() with budget null
lay2 = ConfigLayers(campaign_defaults(), {}).over({"budget": {"rounds": None}, "halt_at_fitness": None})
print("DOOR over budget null ->", lay2.config().optimization.budget)
```

## p2_collide.py
```
import sys; sys.path.insert(0, "."); sys.path.insert(0, "scripts")
from promptpotter.application.datasets.loaders import archive_entry
from promptpotter.domain.arms import PointContext
from promptpotter.domain.opt_search_point import NodePrompt, OptSearchPoint
from promptpotter.domain.pipeline_params import PipelineParams
from promptpotter.domain.pipeline_schema import NodePromptInfo, PipelineNode, NodeKind
from promptpotter.domain.search_point import TaskDecomposition
from tests.factories import pipeline_schema
chain = pipeline_schema([PipelineNode(name="solve", kind=NodeKind.LLM, current_config={"model": "m", "provider": "p"},
                                      prompt_info=NodePromptInfo(fields=("instruction",)), tunes_llm=True)])
pt = OptSearchPoint(prompts={"solve": NodePrompt(fields={"instruction": "Do it."})}).configured(PipelineParams(), chain)
job = PointContext(schema=chain, framing=TaskDecomposition(), demo=[]).job(pt)
a = archive_entry(job, dataset_hash="d", pipeline_schema=chain, cell_identity={"solve": {"judge_instrument": "A"}})
b = archive_entry(job, dataset_hash="d", pipeline_schema=chain, cell_identity={"solve": {"judge_instrument": "B"}})
print("config_key A,B:", a.config_key, b.config_key, "differ:", a.config_key != b.config_key)
print("prompt_fields_id A,B:", a.prompt_fields_id, b.prompt_fields_id, "equal:", a.prompt_fields_id == b.prompt_fields_id)
entries = {e.prompt_fields_id: e for e in (a, b)}
print("regraded_ruler-style index keeps", len(entries), "of 2 entries; individual:", a.individual == b.individual)
```

## p3_cli.py
```
import sys; sys.path.insert(0, ".")
from promptpotter.presentation.cli.commands import launch
from promptpotter.domain.campaign_config import CampaignConfig
for raw in ("unbounded", '"unbounded"', "3", "null"):
    try:
        print(repr(raw), "->", launch._lay({}, CampaignConfig, ["optimization", "nodes", "l1_critique", "config", "transcripts"], raw, "transcripts"))
    except Exception as e:
        print(repr(raw), "->", type(e).__name__, e)
```

## p4_subset.py
```
import sys; sys.path.insert(0, ".")
from promptpotter.domain.layer_fold import Layer, fold_layers, rules_of, wire_patch
from promptpotter.domain.pipeline_schema import NodeSearchNarrowing
rule = rules_of(NodeSearchNarrowing)
under = {"param_keys": ["a", "b"]}
raw = fold_layers(rule, [Layer("d", under), Layer("c", {"param_keys": None})]).document
door = fold_layers(rule, [Layer("d", under), Layer("c", wire_patch(rule, {"param_keys": None}))]).document
print("raw null ->", raw, "| door null ->", door)
```

## p5_shadow.py
```
import os, sys, re, tempfile, pathlib
sys.path.insert(0, ".")
home = pathlib.Path(tempfile.mkdtemp(prefix="pp_shadow_"))
os.environ["PROMPTPOTTER_HOME"] = str(home)
shipped = pathlib.Path("promptpotter/assets/optimizers/potter/pipeline.yaml").read_text(encoding="utf-8")
shadow = re.sub(r"(\n\s*l3_patience:) \d+", r"\1 null", shipped, count=1)
assert shadow != shipped
(home / "optimizers" / "potter").mkdir(parents=True)
(home / "optimizers" / "potter" / "pipeline.yaml").write_text(shadow, encoding="utf-8")
from promptpotter.application.maintenance import landing
try:
    landing.check_treatment("potter")
    print("SHADOW (l3_patience: null) LOADS")
except Exception as e:
    print("SHADOW REFUSED:", type(e).__name__, str(e)[:200].replace("\n", " "))
```

## p7_untyped.py
```
import sys; sys.path.insert(0, ".")
from promptpotter.application.datasets.authored import ConfigLayers, campaign_defaults, declare_run, effective_config
from promptpotter.application.optimizer_manifest import select_optimizer
for node, knob in (("l1_critique", {"temperature": None}), ("l1_critique", {"max_tokens": None}),
                   ("l1_critique", {"reasoning_effort": None}), ("l1_generate", {"model": None})):
    raw = {"optimization": {"nodes": {node: {"config": knob}}}}
    try:
        cfg = ConfigLayers(campaign_defaults(), raw).config()
        spec = declare_run(cfg, dataset_hash="x", connector="llm_only", bench_defaults_pin="p")
        eff = effective_config(spec)
        k = next(iter(knob))
        ran_raw = select_optimizer(cfg.optimization).node_config(node).model_dump().get(k)
        ran_eff = select_optimizer(eff.optimization).node_config(node).model_dump().get(k)
        print(node, knob, "ROUNDTRIP", eff == cfg, "| raw runs", ran_raw, "| declared runs", ran_eff)
    except Exception as e:
        print(node, knob, "->", type(e).__name__, str(e)[:160].replace("\n", " "))
```

## p8_hash.py
```
import sys; sys.path.insert(0, ".")
from promptpotter.infrastructure.llm.base import ChatRequest
from promptpotter.infrastructure.store.llm_reuse_cache import hash_call
m = [{"role": "user", "content": "hi"}]
def h(**k): return hash_call(ChatRequest(model="m", messages=m, **k), provider="openrouter")[:10]
print("effort default vs None:", h(reasoning_effort="default"), h(), "| route [] vs None:", h(route_order=[]), h())
```

## p9_knobrow.py
```
import sys, json; sys.path.insert(0, ".")
from promptpotter.application.optimizers.potter.knobs import EscalationKnobs, TranscriptKnobs
for model, key in ((EscalationKnobs, "l3_patience"), (TranscriptKnobs, "transcripts")):
    prop = model.model_json_schema()["properties"][key]
    options = prop.get("anyOf") or [prop]
    kind = next(o for o in options if o.get("type") != "null")
    enum = kind.get("enum") or ([kind["const"]] if "const" in kind else [])
    print(key, "options:", json.dumps(options), "| row kind:", kind.get("type"), "enum:", enum,
          "nullable:", any(o.get("type") == "null" for o in options))
```

## run.sh
```
#!/bin/sh
# usage: run.sh <tree> <script> [args]
T=$1; shift
cd <scratch>/$T && PYTHONPATH=<scratch>/$T <repo>/.venv/bin/python -X utf8 "$@"
```
