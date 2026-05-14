# M10 Maintenance Pass

Post-cleanup file-organization refactor on top of `5cf3e370`. All
phases shipped in one sweep, no back-compat.

## Trigger

The `5cf3e370` vulture cleanup exposed three structural facts: (1)
three abandoned partial-migration carcasses (`__pycache__/`-only
dirs next to monoliths) blueprinted intended splits; (2)
`domain/round_diagnostics.py` ↔
`application/optimization/round_diagnostics.py` naming collision;
(3) `domain/analysis.py` name no longer matched its gutted content.

## What landed

| Phase | Change |
|---|---|
| P1 | Delete carcass `__pycache__/` dirs; rename `domain/analysis.py` → `escalation_signals.py`; rename `application/optimization/round_diagnostics.py` → `round_analysis.py` (collision fix); flatten `application/datasets/datasets.py` → `application/datasets.py` |
| P2.1 | `infrastructure/llm.py` (955) → `infrastructure/llm/{base,openai_compat,anthropic,rate_limit,json_parse,registry,models}.py` |
| P2.2 | `application/scoring/formula.py` (440) → `formula/{primitives,matchers,compiler,round_scorer,rescore}.py` |
| P2.3 | `presentation/api.py` (1056) → `api/{deps.py, routers/{backends,campaigns,active,datasets}.py}` |
| P2.4 | `presentation/cli/campaign_runner.py` (1395) → facade + `commands/{optimize,sweep,compare,init,_shared}.py` |
| P2.5 | Three validators → `application/optimization/validators/{l1_strict,l1_behavior,l2_l3}.py` |
| P3.1 | `application/optimization/l1.py` (1116) → `l1/{generate,score,resume,execute}.py` |
| P3.2 | `application/optimization/` reshape: new `dispatch/` (hub.py + llm_call.py + schemas.py + pipeline.json), `pobb/` (elimination + elevation), `helpers/` (observers, transitions, decomposition, round_analysis, l1_population, l1_stats, l1_critique) |

## Not in scope

- **`dispatch_hub.py` (851) split** — §0.5 load-bearing; INJECTIONS
  registry + import-time `validate_template()` contract.
- **`presentation/views/live.py` (922), `live_dashboard.py` (808),
  `views/render.py` (777), `campaign_store.py` (722), `runner.py`
  (736)** — cohesive single-concern; size isn't a defect.
- **`shared/statistics.py` merge** — leaf utility, pure stats.
- **`domain/backend.py` merge** — record vs. protocol; keep separate.

## Verify

`ruff check . && ruff format --check . && deptry . && mypy
promptpotter/ && pytest -q` — clean. 180/180 tests. `.ai/SYMBOLS.txt`
regenerated post-pass.
