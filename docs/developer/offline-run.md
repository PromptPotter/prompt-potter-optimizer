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

`--rounds` (each campaign's `max_rounds`) and `--rows` (the synthetic bank) size it.
`--bench-trigger manual` fails on a bench cell sent by the launch, then asks for the pass as an operator does (`grade_line_bench`), so
its `decisions.json` holds the same rounds and headline as the default `at_end` run's.
Run it from the tree under test with that tree's own venv — a worktree probe run through another
tree's interpreter answers for that other tree.

**The gate runs it** — `scripts/gate.py --only offline-run`, under the default arguments, in a
home of its own — whenever a file the engine runs from has changed since it was last green, so an
engine change has every optimizer run end to end and its resume proven without anyone asking; a
docs or webapp edit reruns nothing. `--changed` leaves it out: it proves a landing, not an edit.

**A green run is not taken twice.** A home that already holds the green run of the same arguments
over the same sources — the package, `datasets/`, `examples/`, the two lock files and this script,
by content — is left standing, and the script says `unchanged since green` and exits 0. Name a
different home, or pass `--against`, to run it regardless; a default-argument green by hand is
the gate check's green too (`scripts/kept_verdict.py`).

`--controlled` proves the controlled comparison instead ([`../architecture.md`](../architecture.md)
§ The controlled comparison): two workspaces, each running the arms `--optimizer` names (potter and
capo by default) of one head-to-head, one of them after a foreign campaign on the dataset whose
origin differs — the one layout that puts several optimizers' campaigns in ONE workspace, so the
webapp's Compare tab can read them together. It fails
unless a skip on an arm is refused, an arm searches its prompt's fields alone, each arm's memory
holds only runs it filed, and both arms'
decisions are byte-identical with and without the foreign campaign; `head_to_head.json` beside
them is the evidence read of all three.

## Why it cannot be mistaken for a real run

- **`PROMPTPOTTER_HOME` must be set.** The default workspace is never written. A home that holds
  files but no `offline-run.json` stamp is refused; a stamped one is wiped and rewritten whole.
- **It never bills.** Every `*_KEY` / `*_TOKEN` is blanked for the child processes, the provider keys
  carry a sentinel no provider accepts, every `httpx` transport and `urllib.request.urlopen` is
  answered locally, and name resolution raises — so a client the fakes miss fails instead of
  sending. The run then reads its cycle's spend and fails on any billed dollar.
- **It is stamped twice.** `offline-run.json` at the home's root, and the `OFFLINE — …` label on
  every campaign it mints, which is what the webapp shows.

## What lands where

One workspace per optimizer, `<home>/<optimizer>/`, because the measurement archive and the δ ruler
pool across the campaigns of one workspace — a shared one lets one optimizer's run move another's
decisions. Each campaign is a child process, and as many run at once as the machine's free memory
holds (`scripts/gate.py::memory_budget_mb`) — all of them on a box with room, one after another on
a laptop with none. In each workspace:

| Path | What it is |
|---|---|
| `projects/default/campaigns/<id>/cycles/<cycle>/` | The campaign tree a real run writes: `readout.log`, `dashboard.json`, `rounds/`, the ledger. Browse it with `PROMPTPOTTER_HOME=<home>/<optimizer>` on the server. |
| `requests/NNNN_<node>_<k>.json` | Every optimizer LLM request as sent, in arrival order, beside the fake's answer. |
| `decisions.json` | Every decision the run made, canonical: ids mapped to candidate labels; timestamps, paths and hashes dropped; floats rounded; concurrent records compared as sets. `harness` adds the stop reason, the unrouted URLs and the call counts. |
| `run.log` | The child's stdout and stderr. |
| `resumed/` | The same campaign in a workspace of its own, paused at a round boundary and ended on a session rebuilt from disk. |
| `interrupted/`, `interrupted-twice/` | The first optimizer launched by the CLI's own `main` as a terminal dispatches it, under one and under two SIGINTs, then `resume`d. |

**The interrupted legs are the Ctrl+C contract, run**: `new` must exit 130 with its slot released and
its producer lock dropped — the cycle `paused` after one SIGINT — its `mint-campaign` record must
carry the config and the backend, and `resume` must then end the cycle on a single `start-run`. The
signal is raised inside the child at a fixed cell, since Windows cannot deliver a console Ctrl+C
to one child; what no leg covers is the console's own delivery.

**The run fails unless the resumed campaign decides what the uninterrupted one decided** — every
round, the run's result and the bench headline equal; only the ledger streams a resume appends its
own init records to are left out of the comparison. At `--rounds 1` the only boundary is the
origin's, where a resume replays round 0: there the run fails on a resume that ends without a bench
headline, and prints `MOVED` without failing on it. A campaign that ends on a failed stop
(`domain/phases.py::stop_reason_outcome`) fails its leg whatever it wrote. Both halves run one tree, so the leg catches a
resume that reads what its own writer does not persist, and says nothing about a ledger an earlier
build wrote.

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
behaviour change, and the first differing record says where. Two runs of one tree are byte
identical in both, so any diff is the change's. `--digests` prints the L4 identity digests — the
estimator's source digest and each optimizer's treatment digest — which a change that should not
re-key an inner cell leaves equal.

## What is faked, and how

- **The bank** is synthetic JustLogic-shaped rows for `justlogic-d234`, written to the workspace's
  benchmark rows before the session opens, so nothing is downloaded and nothing licensed is copied.
- **The backend** is TermNorm's wire: one `llm_only` node, whose answer is correct with probability
  `k/10`, `k` read off the `Variant <tag>:` marker in the rendered prompt. Potter's markers follow
  a scripted trajectory that fires L2 and L3; every other marker draws its `k` from its name.
- **The optimizer LLM** answers by node and CALL ORDINAL, never by the prompt's wording. A
  structured call names its node in its response schema and gets a minimal valid instance, shaped
  per potter node. A paper preset's text call is matched to the llm node whose manifest template
  it opens with, and answered in the form that template asks for — a `<prompt>` block, a fenced
  block, or an array.
- **The knobs** are the dataset's `campaign.yaml` under the script's `BENCH` sizes. The template's
  node overlay rides only the optimizer it selects; any other takes its `SCALED` entry, which
  sizes it to the bank, or runs its manifest as declared when it has none. A sampler that
  declares a size knob draws `ROUND_CELLS` whichever optimizer runs. Any optimizer but the
  template's runs under a seeded determinism clamp, and `--controlled` seeds every arm alike:
  unseeded, draws follow the campaign's id, which every run mints anew, so two runs of one tree
  would differ.

**Adding an optimizer** needs nothing here: it runs as its manifest declares, one installed through
its entry points alone included — `examples/optimizer-plugin/` is that case.
