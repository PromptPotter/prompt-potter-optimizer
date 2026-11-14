# The offline run — every optimizer end to end, zero spend

`scripts/offline_run.py` runs one real campaign per installed optimizer through the embedded launch (`run_campaign`), bench pass included, with every network edge answered in-process. It is **the one supported way to prove that a change leaves the loop's decisions, or the optimizer's requests, where they were**, and the one way to produce a peer's campaign tree without a provider account. A developer tool, not an entry point.

## Running it

```bash
PROMPTPOTTER_HOME=.scratch/offline-home python scripts/offline_run.py                    # --optimizer capo: one
python scripts/offline_run.py --digests
```

`--help` lists the sizing and leg flags. `--straight` (each optimizer's straight run, no nested leg) is the webapp's walk world (`scripts/gate.py::_walk_world`). It refuses an interpreter whose `promptpotter` is another tree's.

**The gate runs it** — `scripts/gate.py --only offline-run,offline-run-controlled`, under the default arguments and under `--controlled`, side by side, each in its own home of the clone's (`processes.py::clone_cache`) — whenever a file the engine runs from changed since it was last green; the pre-push hook runs it on a tip pushed to main (`gate.py --push`, in a throwaway worktree on the clone's venv, `gate.py::_proven_in`); pre-commit and `--changed` leave it out. A failed leg fails it; a `MOVED` never does. Each optimizer's workspace is kept green on its own, keyed by content on what that optimizer reaches (`scripts/last_green.py::OFFLINE_RUN_READS` less other optimizers' packages, `scripts/offline_run.py::unreached`) and filed inside the workspace (`scripts/offline_run.py::GREEN`), so another tree rewriting the shared home takes its green along; a green optimizer under the same arguments prints `unchanged since green`; another home reruns all.

`--controlled` proves the controlled comparison instead ([`../architecture.md`](../architecture.md) § The controlled comparison): two workspaces, each minting one head-to-head's arms in one act (`application/embedded_run.py::run_head_to_head`, as the `head-to-head` verb does) and running them one after another, one after a foreign campaign on the dataset. It fails unless an arm searches its prompt's fields alone, each arm's memory holds only runs it filed, and every election read its head-to-head's freeze plus its own lines; `head_to_head.json` is the evidence read.

## Why it cannot be mistaken for a real run

- `PROMPTPOTTER_HOME` must be set; a home holding files but no `offline-run.json` stamp is refused.
- It never bills: every `*_KEY` / `*_TOKEN` is blanked, the provider keys carry a sentinel, every `httpx` transport and `urllib.request.urlopen` is answered locally, name resolution raises, and the run fails on any billed dollar.
- Stamped twice: `offline-run.json` at the home's root, and the `OFFLINE — …` label on every campaign it mints.

## What lands where

One workspace per optimizer, `<home>/<optimizer>/`, because the archive and the δ ruler pool across a workspace's campaigns.

| Path | What it is |
|---|---|
| `data/projects/default/campaigns/<id>/cycles/<cycle>/` | the campaign tree a real run writes, in its own data root beside everything below (`scripts/offline_run.py::data_root`), since every process lands its root at entry; browse it with `PROMPTPOTTER_HOME=<home>/<optimizer>/data` on the server |
| `requests/NNNN_<node>_<k>.json` | every optimizer LLM request as sent, in arrival order, beside the fake's answer |
| `trace.json` | the run's `scripts/offline_run.py::BehaviourTrace`: every decision it made, canonical (ids mapped to labels, timestamps dropped, each content hash labelled by first appearance so a platform's float noise never moves it), the race standings (`p_best`, `paired_breakdown`) among them; `harness` adds the stop reason, unrouted URLs and call counts; the hash of each request; the world version (`scripts/offline_world.py::WORLD`) |
| `resumed/` | the same campaign paused at the first fresh cell measured past a banked walk, round 2 on, and ended on a session rebuilt from disk: the resume replays the walk, its reads included, and runs no proposer; the round it paused in is read off its ledger (`scripts/offline_run.py::paused_at`) |
| `diverged/` | the first optimizer's campaign resumed with `--on-divergence fork` under an edited proposer prompt, then forked twice more (the multi-course sidebar fixture) |
| `rebased/` | potter's campaign resumed with `--from 1` under an L2 proposing the same config-changing fork at every fire; fails unless the line forks and ends on a stop other than `REBASED` within its budget |
| `gated/` | the first optimizer's campaign with the backend refusing every cell, halted at the origin gate twice: resumed still refused, the resume passes the gate once as the operator's override, each cell its origin walk sends sent once; resumed answering, the origin passes on health, each lost cell sent once, on no fork. A launch the gate halts runs no pass, so its lost cells are its origin walk's alone and the bench pass is the proceeding resume's; a refusal's resend wait is not slept (`scripts/offline_world.py::install_network`, `scripts/offline_run.py::run_gated`) |
| `healed/` | potter's campaign with invalid answers scripted by call (`scripts/offline_run.py::HEALS`), each healed on the rung the client's ladder climbs to: a clean re-ask, a schema repair, a refusal's generation salvaged on the first send and on the repair rung, a failed repair healed by the re-ask. Fails unless every scripted call took exactly its script's sends and the campaign decides what the straight one did, less the sends and the failed attempts each call records |
| `wounded/` | potter's, capo's, gepa's and levi's campaigns with calls answered invalid on every send (`scripts/offline_run.py::WOUNDS`): potter's L1 ladder failing every rung (no candidates, charged or the provider's), an L2 fire failing (`layer_parse_failure`), a text node's reply out of its form or empty (its child refused). Fails unless every scripted call took exactly its script's sends and the run recorded each one's parse failure once |
| `l4/` | the L4 fixture (`promptpotter-self/e2e`): an outer potter campaign whose every cell runs an inner potter campaign on the bank in this world, the inner runs' decisions in its trace beside the outer's (`decisions.inner`), the inner requests in `requests/`. Under `--straight --l4` it runs alone after potter's straight run, into that run's data root: the browser walk's world (`scripts/gate.py::_walk_world`) |
| `interrupted/`, `interrupted-twice/` | the first optimizer's one-round campaign under one and two SIGINTs, then `resume`d |
| `controlled/arms/requests/`, `controlled/arm-<optimizer>/` | a head-to-head's arms, minted in one act |

**The run fails unless the resumed campaign decides what the uninterrupted one decided** — every round, the result, the bench headline and every optimizer call count equal, less the init records a resume appends and which of a round's cells it paid or replayed. The interrupted legs are the Ctrl+C contract, run: `new` exits 130 with its slot released and its producer lock dropped, and `resume` ends the cycle on a single `start-run`. A campaign ending on a failed stop (`domain/phases.py::stop_reason_outcome`) fails its leg.

## Proving parity

Run it on the base once and keep that home; every later run names it and prints, per leg that wrote a trace, `UNMOVED`, `no base`, or the trace fields that moved (`decisions.run.<key>`, `decisions.rounds[<n>]`, `decisions.ledger.<list>`, `decisions.harness.<key>`, how many requests, `world`). A home still green for the tree's sources is compared without a run, so a landing asks the gate (`equals-main`, red unless every leg reads `UNMOVED`), which reads the home its `offline-run` check just wrote, `<clone>/.gate_cache/offline-run`:

```bash
python scripts/gate.py --against base                          # offline-run, then equals-main
PROMPTPOTTER_HOME=after python scripts/offline_run.py --against base
diff -r base/<optimizer>/requests after/<optimizer>/requests   # what each optimizer call was SENT
```

Requests moving while decisions hold is a rendering change; decisions moving is a behaviour change; a field the base's trace lacks reads moved. Two runs of one tree are byte identical. A run under the default flags regenerates `promptpotter/assets/behaviour_keys.json`, each built-in optimizer's fingerprint (the hash of its straight run's decisions and requests) and the L4 estimator's (the L4 leg's, its own estimator read out) under `offline_world.py::WORLD`, and exits 1 on a line `wrote …` where it moved: a behaviour change re-keys its optimizer once the file is committed, a refactor leaves it unchanged. `--digests` prints the L4 identity digests, which a change that should not re-key an inner cell leaves equal. `--coverage` prints, per package, the function-body lines the run's processes ran (PEP 669, once a line), which is what a trace can vouch for.

## What is faked, and how

`scripts/offline_world.py` — one LLM provider, one backend, one router over every network edge; the test suite's `offline` fixture installs the same one.

- **The bank**: synthetic JustLogic-shaped rows for `justlogic/d234`, written before the session opens.
- **The backend**: TermNorm's wire, one `llm_only` node whose answer is correct with probability `k/10`, `k` read off the `Variant <tag>:` marker in the rendered prompt, each cell's draw keyed on that prompt and the row — a config edit that leaves the prompt alone answers alike. Potter's markers follow a scripted trajectory whose stalls fire L2 at the pinned `l1_patience`; `FakeBackend(hardness=…)` spreads `k` per row so a δ ruler fits.
- **The optimizer LLM** answers by CALL IDENTITY — asker, node, seed, turn — never by wording or arrival order; an L4 cell's inner campaign asks as its seat and arm (`scripts/offline_run.py::_asker`); a structured call gets a minimal valid instance of its schema, unless `ByCallIdentity.heals` scripts invalid answers for that call (`scripts/offline_world.py::Heal`), its retries keeping its identity, or `ByCallIdentity.wounds` answers it invalid on every send. `ByCallIdentity(rebasing=True)` adds a `fork_proposal` to every L2 answer.
- **The knobs** are the dataset's `campaign` under the script's `BENCH` sizes; one seeded determinism clamp holds every optimizer.

**Adding an optimizer needs nothing here**: it runs as its manifest declares, one installed through entry points included — `examples/optimizer-plugin/` is that case, and `gate.py --only optimizer-plugin` proves it on its `--straight` run (red unless an arm carries `source="example:propose"`).
