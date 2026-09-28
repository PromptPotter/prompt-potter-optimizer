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

`--rounds` (each campaign's `max_rounds`) and `--rows` (the synthetic bank) size it; the test in
`tests/test_numerics.py` § 4 runs every optimizer at the smallest size that still reaches the bench.
Run it from the tree under test with that tree's own venv — a worktree probe run through another
tree's interpreter answers for that other tree.

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
decisions. The optimizers run in parallel, one child process each. In each workspace:

| Path | What it is |
|---|---|
| `projects/default/campaigns/<id>/cycles/<cycle>/` | The campaign tree a real run writes: `readout.log`, `dashboard.json`, `rounds/`, the ledger. Browse it with `PROMPTPOTTER_HOME=<home>/<optimizer>` on the server. |
| `requests/NNNN_<node>_<k>.json` | Every optimizer LLM request as sent, in arrival order, beside the fake's answer. |
| `decisions.json` | Every decision the run made, canonical: ids mapped to candidate labels; timestamps, paths and hashes dropped; floats rounded; concurrent records compared as sets. `harness` adds the stop reason, the unrouted URLs and the call counts. |
| `run.log` | The child's stdout and stderr. |

## Proving parity

Run it on the base and on the change, each from its own tree, then:

```bash
diff -r base/<optimizer>/requests after/<optimizer>/requests   # what each optimizer call was SENT
diff base/<optimizer>/decisions.json after/<optimizer>/decisions.json
```

Requests differing while decisions match is a rendering change; decisions differing is a
behaviour change, and the first differing record says where. Two runs of one tree are byte
identical in both, so any diff is the change's. `--digests` prints the L4 identity digests — the
estimator's source digest and each optimizer's prompt source digest — which a change that should
not re-key an inner cell leaves equal.

## What is faked, and how

- **The bank** is synthetic JustLogic-shaped rows for `justlogic-d234`, written to the workspace's
  benchmark rows before the session opens, so nothing is downloaded and nothing licensed is copied.
- **The backend** is TermNorm's wire: one `llm_only` node, whose answer is correct with probability
  `k/10`, `k` read off the `Variant <tag>:` marker in the rendered prompt. Potter's markers follow
  a scripted trajectory that fires L2 and L3; every other marker draws its `k` from its name.
- **The optimizer LLM** answers by node and CALL ORDINAL, never by the prompt's wording. A
  structured call names its node in its response schema and gets a minimal valid instance, shaped
  per potter node. A paper preset's text call is matched to the llm node whose manifest template
  it opens with, and answered in the format that node parses.
- **The knobs** are the dataset's `campaign.yaml` under the script's `BENCH` sizes; potter keeps the
  dataset's own node overlay, and each peer's paper configuration is scaled down in `NODES`. A peer
  also runs under a seeded determinism clamp: unseeded, its draws follow the campaign's id, which
  every run mints anew, so two runs of one tree would differ.

**Adding an optimizer** makes the run fail loudly until the script knows it: a `NODES` entry for
its scaled-down knobs, and a case in `FakeLLM._text` for each text-answering llm node.
