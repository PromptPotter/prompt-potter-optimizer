# w11-connectors3: area 08, round 3: a connector's identity and tunables are its own (cloud · Opus)

`briefs/common.md`, `briefs/w10-shared.md` and `briefs/w11-shared.md` bind you. SPEC: `briefs/map.md`
§ 08 and the debt doc's `## 08` section, plus one row each from `## 05` and `## 07`.

Target: connectors are the only place a backend's identity is named; a built-in is one module binding
its declaration; a tunable rides its node's overlay (`assets/datasets/CLAUDE.md` § Sole route).

Links, deletion first. Work each row along its root:
1. **A machine slot claims its producer on the event loop**. The claim is taken off the loop at the
   async door, or at the launch that owns the process. Re-test it as the row says.
2. **Per-connector tunables are install globals** (filed under 07; w11-config2 handed it here). Its
   holders are `connectors/termnorm.py`, `n8n.py` and `dbllmbench.py`. Add the symptom w11-config2 found
   beside it: `connectors/harbor.py:195`'s `(agent or {}).get("name") or "terminus-2"` is a code default.
3. **Harbor's identity is named in core**. The panel builder and recipe move to a declaration that core
   reads. `connectors/` may not import `application/` (`_LAYERING`), so the declaration lives below.
4. **dbllmbench's cells send on the box's key read off `settings`, not `sending_key`** (filed under 05)
   and **A remote backend's cells ask the bound account** (`backend.py::BackendClient.cells_paid_by`).
   One root: the payer is resolved per cell by `wallet.py`.

Leave open: the TermNorm rows (xconn 3 and 4, which wait on TermNorm reaching the box) and **The box keeps
21 dead producers' locks** (the box is the operator's).

Census: `connectors/**` and `infrastructure/backend.py`. Give each module with no row a verdict.

OWNS: `connectors/**`, `infrastructure/backend.py`, `infrastructure/docker_host.py`, `infrastructure/llm/**`
less `pricing.py`, `application/datasets/harbor_panel.py`, the settings fields link 2 removes, and their
tests. Do not edit `domain/pipeline_overlay.py` or `layer_fold.py`, which w11-config3 owns; a tunable
rides the overlay as it stands at base. Other chains running at the same time: w11-config3, w11-spend2 (area 05's files, `wallet.py` included: call
`sending_key`, do not change it) and w11-jobs3 (`jobs/**`).

DONE WHEN:
- Each row is re-tested through a door, before and after.
- `git grep -nE "terminus-2|env_reward" -- promptpotter/application promptpotter/config` is empty.
- Equals-main names each move, and the keys are named.
- `python scripts/gate.py` is green.
