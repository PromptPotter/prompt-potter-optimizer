# The offline run — every optimizer end to end, zero spend

`scripts/offline_run.py` runs one real campaign per installed optimizer through the embedded launch (`open_session` → `run_campaign`), bench pass included, with every network edge answered in-process. It is **the one supported way to prove that a change leaves the loop's decisions, or the optimizer's requests, where they were**, and the one way to produce a peer's campaign tree without a provider account. A developer tool, not an entry point.

## Running it

```bash
PROMPTPOTTER_HOME=.scratch/offline-home .venv/Scripts/python.exe scripts/offline_run.py
PROMPTPOTTER_HOME=.scratch/offline-home .venv/Scripts/python.exe scripts/offline_run.py --optimizer capo
.venv/Scripts/python.exe scripts/offline_run.py --digests
```

`--rounds` and `--rows` size it; `--bench-trigger manual` fails on a bench cell sent by the launch, then asks for the pass as an operator does (`application/embedded_run.py::grade_bench`). It refuses an interpreter whose `promptpotter` is another tree's, naming the `PYTHONPATH` that pins its own.

**The gate runs it** — `scripts/gate.py --only offline-run`, under the default arguments and then `--controlled`, each in its own home of the clone's (`processes.py::clone_cache`) — whenever a file the engine runs from changed since it was last green; the pre-push hook runs it on a tip pushed to main (`gate.py --push`, in a throwaway worktree on the clone's venv, `gate.py::_tip_checkout`); pre-commit and `--changed` leave it out. A failed leg fails it; a `MOVED` never does. Each optimizer's workspace is kept green on its own, keyed by content on what that optimizer reaches (`scripts/last_green.py::OFFLINE_RUN_READS` less other optimizers' packages, `scripts/offline_run.py::unreached`); a green optimizer under the same arguments prints `unchanged since green`; another home or `--against` reruns all. Its straight leg's `potter/` is the world the `playwright` check walks, an input it never brings along: the full gate's py lane builds it before that `alone` check, CI's `webapp` job in a step of its own (`gate.py --only offline-run`), and the gate stamps it with the key that built it (`gate.py::Check.built`), so a world another worktree built reruns the leg and refuses the walk.

`--controlled` proves the controlled comparison instead ([`../architecture.md`](../architecture.md) § The controlled comparison): two workspaces, each minting one head-to-head's arms in one act (`application/embedded_run.py::run_head_to_head`, as the `head-to-head` verb does) and running them one after another, one after a foreign campaign on the dataset. It fails unless an arm searches its prompt's fields alone, each arm's memory holds only runs it filed, and every election read its head-to-head's freeze plus its own lines; `head_to_head.json` is the evidence read.

## Why it cannot be mistaken for a real run

- `PROMPTPOTTER_HOME` must be set; a home holding files but no `offline-run.json` stamp is refused.
- It never bills: every `*_KEY` / `*_TOKEN` is blanked, the provider keys carry a sentinel, every `httpx` transport and `urllib.request.urlopen` is answered locally, name resolution raises, and the run fails on any billed dollar.
- Stamped twice: `offline-run.json` at the home's root, and the `OFFLINE — …` label on every campaign it mints.

## What lands where

One workspace per optimizer, `<home>/<optimizer>/`, because the archive and the δ ruler pool across a workspace's campaigns. Each campaign is a child process, as many as free memory holds (`scripts/processes.py::RamBudget`, `OFFLINE_RUN_CHILDREN_MAX`).

| Path | What it is |
|---|---|
| `projects/default/campaigns/<id>/cycles/<cycle>/` | the campaign tree a real run writes; browse it with `PROMPTPOTTER_HOME=<home>/<optimizer>` on the server |
| `requests/NNNN_<node>_<k>.json` | every optimizer LLM request as sent, in arrival order, beside the fake's answer |
| `decisions.json` | every decision the run made, canonical (ids mapped to labels, timestamps and hashes dropped); `harness` adds the stop reason, unrouted URLs and call counts |
| `run.log` | the child's stdout and stderr |
| `resumed/` | the same campaign paused at the first fresh cell measured past a banked walk, round 2 on, and ended on a session rebuilt from disk: the resume replays the walk, its reads included, and runs no proposer (`scripts/offline_run.py::BOUNDARY_RESUMED` pause at the round boundary instead); the round it paused in is read off its ledger (`scripts/offline_run.py::paused_at`) |
| `diverged/` | the first optimizer's campaign resumed with `--fork-on-divergence` under an edited proposer prompt, then forked twice more (the multi-course sidebar fixture) |
| `rebased/` | potter's campaign resumed with `--from 1` under an L2 proposing the same config-changing fork at every fire; fails unless the line forks and ends on a stop other than `REBASED` within its budget |
| `gated/` | the first optimizer's campaign with the backend refusing every cell, halted at the origin gate twice: resumed still refused, the resume passes the gate once as the operator's override, each cell its origin walk sends sent once; resumed answering, the origin passes on health, each lost cell sent once, on no fork; a refusal's resend wait is not slept (`scripts/offline_world.py::install_network`, `scripts/offline_run.py::run_gated`) |
| `interrupted/`, `interrupted-twice/` | the first optimizer's one-round campaign under one and two SIGINTs, then `resume`d |
| `controlled/arms/requests/`, `controlled/arm-<optimizer>/` | a head-to-head's arms, minted in one act |

**The run fails unless the resumed campaign decides what the uninterrupted one decided** — every round, the result, the bench headline and every optimizer call count equal, less the init records a resume appends and which of a round's cells it paid or replayed. The interrupted legs are the Ctrl+C contract, run: `new` exits 130 with its slot released and its producer lock dropped, and `resume` ends the cycle on a single `start-run`. A campaign ending on a failed stop (`domain/phases.py::stop_reason_outcome`) fails its leg.

## Proving parity

Run it on the base once and keep that home; every later run names it and prints, per optimizer, whether its decisions and requests moved:

```bash
PROMPTPOTTER_HOME=after python scripts/offline_run.py --against base
diff -r base/<optimizer>/requests after/<optimizer>/requests   # what each optimizer call was SENT
diff base/<optimizer>/decisions.json after/<optimizer>/decisions.json
```

Requests differing while decisions match is a rendering change; decisions differing is a behaviour change. Two runs of one tree are byte identical. `--digests` prints the L4 identity digests, which a change that should not re-key an inner cell leaves equal.

## What is faked, and how

`scripts/offline_world.py` — one LLM provider, one backend, one router over every network edge; the test suite's `offline` fixture installs the same one.

- **The bank**: synthetic JustLogic-shaped rows for `justlogic/d234`, written before the session opens.
- **The backend**: TermNorm's wire, one `llm_only` node whose answer is correct with probability `k/10`, `k` read off the `Variant <tag>:` marker in the rendered prompt, each cell's draw keyed on that prompt and the row — a config edit that leaves the prompt alone answers alike. Potter's markers follow a scripted trajectory whose stalls fire L2 at the pinned `l1_patience`; `FakeBackend(hardness=…)` spreads `k` per row so a δ ruler fits.
- **The optimizer LLM** answers by CALL IDENTITY — node, seed, turn — never by wording or arrival order; a structured call gets a minimal valid instance of its schema. `ByCallIdentity(rebasing=True)` adds a `fork_proposal` to every L2 answer.
- **The knobs** are the dataset's `campaign` under the script's `BENCH` sizes; one seeded determinism clamp holds every optimizer.

**Adding an optimizer needs nothing here**: it runs as its manifest declares, one installed through entry points included — `examples/optimizer-plugin/` is that case, and `gate.py --only optimizer-plugin` proves it (red unless an arm carries `source="example:propose"`).
