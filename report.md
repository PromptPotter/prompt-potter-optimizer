# narr-3 report

Base `dcc98fd75`. Arc `arc/narr-3` at `0ca5afb`. Chain total `git diff --shortstat dcc98fd75 HEAD -- promptpotter`: 20 files, +264/-214 (net +50).

## Link 1: `bb3c9cd` refactor(narr): an act banks facts; the readout words and cuts them

- **Size:** promptpotter +192/-175 (net +17).
- **LEAN:** the code grew because the readout now builds the Start/Parent/candidate columns and the parent preview from `RoundStatement.head` and the arms, work that writers used to pre-build. `Frontier.parent_fitness_text`, `Cycle.parent_fold`, round.py `_flat` and `ReadoutProjection.for_campaign` are deleted. The surface-ledger baseline is raised to 109807, with the reason stated.
- **Re-test:** at base, `phase_acts.py` carried title/tag/lines/headline/details/parent_prompt/columns. At the tip it carries none of them.
- **Equals-main (`offline_run.py --against`):** all 20 legs UNMOVED. `decisions.ledger.phases` compares only (round, phase, step), so it does not move either.
- **Digests:** treatment:potter `fc9104aec01c97b0`→`91bc214535b17797` and treatment:capo-pobb `3b1dcd1e619c480a`→`c84f6419ab6e9853`. Both moved because the behaviour key hashes the potter package source (members.py, escalation.py). estimator, capo, gepa and levi are unchanged.
- **Readout before/after:** step boxes now show cut fact lines. The stale `llm_only.prompt` row is gone from the SP table, because resolved_params strips the rendered prompt; the prompt fields still show. The parent-prompt line is identical. The rest of the diff is cache-hit noise.
- **Wipe:** cycles run data. Landing keys:
  - step_opened title/tag/lines → facts
  - step_closed headline/details → facts + audit
  - propose_opened parent_prompt/parent_fitness → parent_round
  - propose_closed columns/node_param_keys/collapses → removed

## Link 2: `0ca5afb` refactor(narr): one door to a run's ledger, the scope; one admission

- **Size:** promptpotter +72/-39 (net +33).
- **LEAN:** the code grew from the `declare_on_cycle` mute/scope door, `CampaignStore.appending`, and `verb_ledger`'s hop refusal. Baseline raised to 109840.
- **Re-test:** the `campaigns.append(` grep over runner + run_observers found 6 sites at base and 0 at the tip.
- **Probe render:** under `scoped(ledger=None)`, the declaration makes 0 store calls and writes no file.
- **Equals-main:** 20/20 UNMOVED. Digests: unchanged by this link.

## Gate

`python scripts/gate.py` at the tip: 29 checks green.

## Decided in-run

- The name is `declare_on_cycle`, because `declare` collides with `jobs/mint.py::declare`.
- `RunAdmission.of(campaign_refusal=)` is required. Loop commands join it through `admission.py::admitted`.
- No file is shared with coupon.

## Rows closed

- **The typed acts still bank render text**
- **A run's ledger has two doors**

## Rows to file

- **A phase control still appends around the scope.** `application/run_phase_control.py` keeps one `campaigns.append(`, outside the brief's grep. Route it through `declare_on_cycle` or name why a pause/resume control writes outside a run scope.
- **Muted and unbound read the same.** `llm/telemetry.py::active_cycle_ledger` returns None for both a muted scope and UNBOUND, so `verb_ledger` under a muted scope opens a fresh ledger. The scope should report the three states distinctly.

## Symptoms

- `promptpotter/application/run_phase_control.py`: a direct `campaigns.append(`. Suspected root: the one-door rule is unenforced outside runner/.
- `promptpotter/infrastructure/llm/telemetry.py::active_cycle_ledger`: mute is indistinguishable from unbound. Suspected root: `None` doubles as the mute sentinel.
