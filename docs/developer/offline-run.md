# The offline run — every optimizer end to end, zero spend

`scripts/offline_run.py` runs one real campaign per installed optimizer — potter, CAPO, LEVI,
GEPA, and any preset added beside them — through the embedded launch (`open_session` →
`run_campaign`), bench pass included, with every network edge answered in-process. It is **the one
supported way to prove that a change leaves the loop's decisions, or the optimizer's requests,
where they were**, and the one way to produce a peer's campaign tree without a provider account.

It is a developer tool, not an entry point: nothing an operator launches can reach it, so it owes
no `<entry-point-parity>` surface.

## Running it

```bash
PROMPTPOTTER_HOME=.scratch/offline-home .venv/Scripts/python.exe scripts/offline_run.py
PROMPTPOTTER_HOME=.scratch/offline-home .venv/Scripts/python.exe scripts/offline_run.py --optimizer capo
.venv/Scripts/python.exe scripts/offline_run.py --digests
```

`--rounds` (each campaign's `budget.rounds`; the interrupted legs run one round, their contract being
the pause and the resume rather than the search) and `--rows` (the synthetic bank) size it.
`--bench-trigger manual` fails on a bench cell sent by the launch, then asks for the pass as an operator does (`application/embedded_run.py::grade_bench`), so
its `decisions.json` holds the same rounds and headline as the default `at_end` run's.
Run it from the tree under test with that tree's own venv — a worktree probe run through another
tree's interpreter answers for that other tree.

**The gate runs it** — `scripts/gate.py --only offline-run`, under the default arguments, in the
clone's home (`processes.py::clone_cache`, one for every worktree) — whenever a file the engine runs from has changed since it was last green, so an
engine change has every optimizer run end to end and its resume proven without anyone asking; a
docs or webapp edit reruns nothing. A failed leg fails it; a `MOVED` never does. **The pre-push
hook runs it on each pushed tip** with every other CI check (`gate.py --push`), in a throwaway `git worktree` of that tip
borrowing the pushing tree's `.venv` and `webapp/node_modules` as links
(`gate.py::_tip_checkout`), so its verdict is the tip's whatever the working tree holds. The green
store is the clone's (`last_green.py::LastGreen`), keyed by content, so a tip already green in any
worktree is a stamp read. The pre-commit hook and `--changed` leave it out: it proves a
landing, not an edit. CI runs it on every push.

**A green optimizer is not run twice.** Each optimizer's workspace, with every leg nested in it, is
kept green on its own, keyed by content on what that optimizer reaches:
`scripts/last_green.py::OFFLINE_RUN_READS` less the member packages and manifest directories of
other optimizers it neither imports nor resolves (`scripts/offline_run.py::unreached`). A shared
engine file reruns every optimizer; one optimizer's own file reruns that one. A home holding a
green optimizer under the same arguments prints `unchanged since green` for it; name another home
or pass `--against` to rerun all. A default-argument green by hand is the gate's green too.

`--controlled` proves the controlled comparison instead ([`../architecture.md`](../architecture.md)
§ The controlled comparison): two workspaces, each minting one head-to-head's arms (`--optimizer`,
potter and capo by default) in one act through `application/embedded_run.py::run_head_to_head`, as
the `head-to-head` verb mints them, and running them one after another, one workspace after a
foreign campaign on the dataset — the one layout putting
several optimizers' campaigns in ONE workspace, for the webapp's Compare tab. It fails unless a
skip on an arm is refused, an arm searches its prompt's fields alone, each arm's memory holds only
runs it filed, and every election read its head-to-head's freeze plus its own lines; whether the
foreign campaign moved the decisions is printed as a reading. `head_to_head.json` is the evidence read.

## Why it cannot be mistaken for a real run

- **`PROMPTPOTTER_HOME` must be set.** The default workspace is never written. A home that holds
  files but no `offline-run.json` stamp is refused; a stamped one keeps each workspace still green
  under the same arguments and rewrites the rest.
- **It never bills.** Every `*_KEY` / `*_TOKEN` is blanked for the child processes, the provider keys
  carry a sentinel no provider accepts, every `httpx` transport and `urllib.request.urlopen` is
  answered locally, and name resolution raises — so a client the fakes miss fails instead of
  sending. The run then reads its cycle's spend and fails on any billed dollar.
- **It is stamped twice.** `offline-run.json` at the home's root, and the `OFFLINE — …` label on
  every campaign it mints, which is what the webapp shows.

## What lands where

One workspace per optimizer, `<home>/<optimizer>/`, because the measurement archive and the δ ruler
pool across the campaigns of one workspace — a shared one lets one optimizer's run move another's
decisions. Each campaign is a child process, as many at once as free memory holds
(`scripts/gate.py::RamBudget`), never past the core count or `OFFLINE_RUN_CHILDREN_MAX`. In each workspace:

| Path | What it is |
|---|---|
| `projects/default/campaigns/<id>/cycles/<cycle>/` | The campaign tree a real run writes: `readout.log`, `dashboard.json`, `rounds/`, the ledger. Browse it with `PROMPTPOTTER_HOME=<home>/<optimizer>` on the server. |
| `requests/NNNN_<node>_<k>.json` | Every optimizer LLM request as sent, in arrival order, beside the fake's answer. |
| `decisions.json` | Every decision the run made, canonical: ids mapped to candidate labels; timestamps, paths and hashes dropped; floats rounded; concurrent records compared as sets. `harness` adds the stop reason, the unrouted URLs and the call counts. |
| `run.log` | The child's stdout and stderr. |
| `resumed/` | The same campaign in a workspace of its own, paused at a round boundary and ended on a session rebuilt from disk. |
| `diverged/` | The first optimizer's campaign resumed with `--fork-on-divergence` under an edited proposer prompt: the parent ends on one `REBASED` stop, the fork runs on, and two further forks give one campaign four courses (the multi-course sidebar fixture). |
| `rebased/` | Potter's campaign paused at round 1, beside a prior campaign, resumed with `--from 1` under an L2 proposing the same config-changing fork at every fire: it fails unless the line forks, each fork ends on a stop that is not a failure, the line's cycles together run no more rounds than its budget, and the line ends on a stop other than `REBASED`. |
| `interrupted/`, `interrupted-twice/` | The first optimizer's one-round campaign launched by the CLI's own `main` as a terminal dispatches it, under one and under two SIGINTs, then `resume`d. |
| `controlled/arms/requests/`, `controlled/arm-<optimizer>/` | A head-to-head's arms share one world, minted in one act: every arm's requests land in one directory, and each arm's `decisions.json` `harness` counts the whole act. |

**The interrupted legs are the Ctrl+C contract, run**: `new` must exit 130 with its slot released and
its producer lock dropped — the cycle `paused` after one SIGINT — its `mint-campaign` record must
carry the config and the backend, and `resume` must then end the cycle on a single `start-run`. The
signal is raised inside the child at a fixed cell, since Windows cannot deliver a console Ctrl+C
to one child; what no leg covers is the console's own delivery.

**The run fails unless the resumed campaign decides what the uninterrupted one decided** — every
round, the result and the bench headline equal, less the init records a resume appends. At
`--rounds 1` a resume replays round 0: it fails without a bench headline and prints `MOVED`
otherwise. A campaign ending on a failed stop (`domain/phases.py::stop_reason_outcome`) fails its
leg. Both halves run one tree, so the leg catches a resume reading what its writer does not
persist, never a ledger an earlier build wrote.

## Proving parity

Run it on the base once and keep that home; every later run names it and prints, per optimizer,
whether its decisions and its requests moved. A `MOVED` is a reading, never a failure — `diff` the
two files to see where:

```bash
PROMPTPOTTER_HOME=after python scripts/offline_run.py --against base
diff -r base/<optimizer>/requests after/<optimizer>/requests   # what each optimizer call was SENT
diff base/<optimizer>/decisions.json after/<optimizer>/decisions.json
```

Requests differing while decisions match is a rendering change; decisions differing is a
behaviour change. Two runs of one tree are byte identical, so any diff is the change's.
`--digests` prints the L4 identity digests (estimator source, each optimizer's treatment), which
a change that should not re-key an inner cell leaves equal.

## What is faked, and how

The fake world is `scripts/offline_world.py` — one LLM provider, one backend, one router over
every network edge — and the test suite's `offline` fixture installs the same one; this script adds
only the bank and the call-identity policy below.

- **The bank** is synthetic JustLogic-shaped rows for `justlogic/d234`, written to the workspace's
  benchmark rows before the session opens, so nothing is downloaded and nothing licensed is copied.
- **The backend** is TermNorm's wire: one `llm_only` node, whose answer is correct with probability
  `k/10`, `k` read off the `Variant <tag>:` marker in the rendered prompt. Potter's markers follow
  a scripted trajectory whose stalls fire L2 at the leg's pinned `l1_patience`; every other marker draws its `k` from its name.
  `FakeBackend(hardness=…)` spreads `k` per row, so a δ ruler fits; at `0` the ruler is flat and a
  rewind is dropped.
- **The optimizer LLM** answers by CALL IDENTITY — the node, the request's seed, the call's turn
  among sends sharing both — never by wording or arrival order. A structured call gets a minimal
  valid instance of its response schema; a paper preset's text call is matched to the manifest
  template it opens with and answered in the form that template asks for. `ByCallIdentity(rebasing=True)`
  adds a `fork_proposal` unlocking `schema_field_rename` to every L2 answer; off, no answer carries one.
- **The knobs** are the dataset's `campaign` under the script's `BENCH` sizes; an optimizer the
  template's overlay does not select takes its `SCALED` entry or runs its manifest as declared, and
  a sampler's size knob draws `ROUND_CELLS`. One seeded determinism clamp holds every optimizer:
  unseeded, draws follow the freshly minted campaign id and two runs of one tree would differ.

**Adding an optimizer** needs nothing here: it runs as its manifest declares, one installed through
its entry points alone included. `examples/optimizer-plugin/` is that case, and the gate proves it
(`gate.py --only optimizer-plugin`): installed into a scratch venv layered on the tree's, run
`--optimizer example`, red unless an arm carries `source="example:propose"`.
