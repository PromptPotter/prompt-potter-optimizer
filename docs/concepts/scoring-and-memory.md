# Scoring and Memory

**Traces are facts. Scores are policy.** A trace records what the pipeline did; a score judges *over* a
trace under the active formula, on demand. Traces are written once and never edited.

## The archive

**Archive vs ledger** is [`../architecture.md`](../architecture.md) § Measurement archive's; scopes and filing
are [`store/CLAUDE.md`](../../promptpotter/infrastructure/store/CLAUDE.md) § The archive is not scoped by
campaign. It is rooted per archive home (`build_stores`: `Stores.archive_home`), the tenant's own unless the
host names `settings.SHARED_ARCHIVE` ([`../decisions.md`](../decisions.md) § 0008).

```
ON DISK (the database)                      DERIVED (folded from disk)
measurements/                               MeasurementArchive
  index.jsonl          configuration × bank      └─ SampleIndex (per sample)
  configs/{config_key}.json
  cells/{config_key}.jsonl  one line per answer
```

**`MeasurementArchive` is the only gateway**; programmatic reads go through
`infrastructure/store/archive_queries.py`. `SampleIndex` is folded by `refresh()`, its fold persisted
(`measurements/sample_folds/{dataset_hash}.jsonl`) and revalidated against the active formula. Cache reuse and
cross-run digests are derived views over the archive, never a second store.

**A cell is one configuration measuring one sample, and it keeps every answer it was given.** An answer is one
appended line, filed under the node-chain prefix that PRODUCED it (`terminal_node`), so every configuration
sharing that prefix reads it. A configuration × bank's answers are its **population**; each reader declares its
take over it, default `infrastructure/store/measurement_archive.py::standing`. The archive knows no run, campaign or round: a walk
records the answers it took (`ScoredWalk.cells`) and the ledger names them.

**Every row is FACTS, never a grade** (§ Rescore-on-load). A filed answer is `domain/sample.py::FiledAnswer` —
a `MeasuredCell` plus `config_key`, `dataset_hash`, `role`, `source`, `provenance`, `created_at`; the index entry
is `ArchiveEntry`. **`role` is WHY the scoring pass ran** (`shared/measurement_context.py::MeasurementRole`);
which roles a reading may see is its `RoleScope`, part of its identity, so readings in two scopes never pair.

**No key names an optimizer, a position, a dataset name or a backend** — an arm two optimizers both propose
replays free. `node_configs` is canonical identity and stays deterministic from pipeline params.

**Write path:** `application/datasets/loaders.py::archive_entry` names the configuration →
`MeasurementArchive.file_answers` appends one line per answer the cell did not hold; a replay files nothing.
**Read paths:** `archive_queries.py`'s `load_population` / `list_populations` (inside the scope
`bind_memory_scope` gives the task) and `walked_answers`; `MeasurementArchive.measurements_for_config`.

**The content hashes are not interchangeable:**

| Hash | Names | Carried on |
|---|---|---|
| `config_key(node_configs)` | where a configuration's cells are FILED — one per prefix of its node chain | `cells/{config_key}.jsonl` |
| `PipelineSchema.sp_hash(params)` | the searchpoint over the same resolved node configs | every index entry's `prompt_fields_id`; a ruler row (`domain/ruler.py::RulerRow`); `reading.sp_hash`, stamped on the `Measuring` arm state |
| `JobSearchPoint.content_hash(dataset)` | a campaign's root; files nothing | the root |
| `OptSearchPoint.id` | the individual's own content, less the campaign's framing and the demo pool | `CandidateFacts.individual` |

**Never re-derive `sp_hash` downstream:** `resolved_params` is the node configs with each rendered `prompt`
stripped, and the hash covers that prompt — a recompute addresses no cell, and nothing raises.

## The bench headline

At run init the bank splits into a **search split**, a held-out **bench set** and a **demo pool**
(`CampaignConfig.dataset_split`, over DISTINCT samples). Every optimizer node sees the search split alone —
the demo pool only as shot material — and no node ever sees the bench set, the check-in model included. The
bench scores each optimizer's result on the bench set with ONE evaluator; that is the headline every surface
serves and `export.json` reports.

**Every reading carries two columns, `accuracy` and `composite`, each with its own band and paired lift, and
names which is the headline** (`domain/bench.py::BENCH_HEADLINE`). **The bench grades the pick the optimizer
DECLARED** — its selector's last selection (`Election.selected`, `RoundResult.head`) — never one the bench
makes by comparing rounds read on different rows
([`../research/benchmarks.md`](../research/benchmarks.md) § The winner's own number is biased upward). A line
that closed no round declared none; which stops end a line holding one is `domain/phases.py::STOP_REASON_INFO`'s
`grades_selection`.

**The graded result is the CAMPAIGN's, never a cycle's.** Each pass is a `bench:graded` record on the ledger
of the cycle that sent it; the campaign's passes are the fold of its LINE's ledgers
(`infrastructure/store/campaign_store/ledger_scan.py::scan_bench_passes`), launch clocks likewise
(`application/runner/campaign_result.py::line_launches`). Only the cycle holding the line writes either.

**When passes are sent is the campaign's `bench_trigger`; the default (`manual`) sends none**: the `bench` verb
(`application/runner/grade_bench.py::grade_line_bench`) sends both on demand; `at_end` sends the origin's before
round 1 and the selection's at the end; `each_round` adds every selecting round. Where the bench stands is ONE
served `BenchStatus` (`domain/bench.py::bench_status`), which the browser, the terminal and the verb's admission
all read.

## Rescore-on-load

**The archive holds no score at all.** Its write seam strips every grade a row carries
(`domain/scoring.py::GRADE_KEYS`), so a banked row is what was measured — answer, tokens, latency, the backend's
observations, a judge's banked verdict. A row in MEMORY may carry grades for the round's own use; the seam
keeps them off disk.

Every read grades under a scorer it names, through ONE derivation: facts → graded cells
(`domain/scoring.py::Scorer.grade`, pure; a `CellSheet` carries the grades and the scorer id) → the population
(`CellSheet.scoreable`) → folds → readings. Fresh samples, cache hits, the δ ruler, cross-campaign reads and every
mask take that step, so rows banked under one formula read under another exactly as a fresh run would. **A lens
is a scorer, not a formula over aggregates**: `score:F` is the campaign's scoring block with `per_cell` set to
`F` ([`../operations/mask-projection.md`](../operations/mask-projection.md)). **The bench headline is one more
such reading**: a pass banks facts (`domain/bench.py::BenchPass`) and `application/runner/bench.py::read_bench` is
the one derivation to a `BenchScore`, stamped with its `scorer_id`. A formula that cannot be evaluated
(`ScoringFormulaError`) halts the run; it is never written into a row. A formula CHANGE on a running cycle is
[`../operations/persistence-and-state.md`](../operations/persistence-and-state.md) § Changing the composite
formula — fork, never swap.

**Two costs**, answering different questions:

- **The bill** — money that left the account; cache hits contribute nothing. The headline, and what the spend
  budget caps; a published figure also carries the raw tokens and the price table's date and never omits the
  optimization spend ([`../research/external-constraints.md`](../research/external-constraints.md) § Cost reporting).
- **The incurred cost** — what the search would cost against a cold cache, cache hits priced from the tokens
  they recorded, each cell once per campaign. What a *measurement of a candidate* divides by; a bench lift
  divides by the SEARCH's share alone (`domain/spend.py::SEARCH_KINDS`). **A controlled arm's ceiling caps this
  one** (`CeilingMeter` `search_incurred`): arms of one head-to-head may replay each other's cells, so a
  ceiling on the bill would hand whichever arrived second a larger search.

**Either cost lands in a bucket named for who spent it** (`domain/spend.py::TokenUsageKind`: Connector /
Optimizer / Judge / Diagnostic / Bench). A bucket is never an exemption: a `bill` ceiling counts all five, a
`search_incurred` one counts `SEARCH_KINDS` alone.

**Two caches, and the word only belongs to one.** A **replay** — our archive answered, nothing billed — is a
boolean: `TokenUsageRecord.cached`. A **prefix-cache read** — a provider served the call and discounted part of
its input — is a token count, a SUBSET of the input: `TokenUsageRecord.cache_read_tokens`, rendered through
`domain/spend.py::TokenAccount.cache_share`, whose required `replayed` kwarg stops a replay being shown a discount.
`cached` is the replay, never the count: `account_spend` drops a record from the roll-up whenever `cached` is
truthy. The provider cache is per-replica and warms by repetition, and it is why the optimizer prompt renders
everything volatile last (`opt_search_point.py::OptimizerPromptTemplate.RENDER_ORDER`).

## Deprecated samples

**Which advisories derive a fatal or infra code** — owned by
[`../developer/self-healing-internals.md`](../developer/self-healing-internals.md). A deprecated result is
evicted from cache and re-measured, **but it still counts** — a refusal or a truncation is what the prompt
produced, so it is the miss the formula grades. The trace stays in `measurements/` either way.

## Which rows a reading counts

**A reading's population is the population that was sent.** One predicate decides it for every number an
optimizer elects on and the bench headline alike — `domain/scoring.py::Scorer.grade` (stamping
`GradedCell.scored`; list form `classification.py::scoreable_rows`). Three kinds of row leave: a provider or
machine fault (`shared/errors.py::ERROR_IS_CHARGED`), a cell that ran to its end with nothing to grade
(`UNSCOREABLE`), and a row the formula cannot grade (`unscored`). Everything the configuration caused stays: a
refusal, a truncation or an empty answer is graded, and a charged error is a miss at 0.0 wherever a label
defines one. A bench pass that ends with more rows lacking a verdict than its split's `tolerance` reads nothing.

**A cell measured more than once is one piece of evidence.** Its standing row is its last row carrying a
verdict, else its last row; every statistic reads one standing row per cell (`domain/scoring.py::CellSheet`).
The rows all stay on the sheet, in walk order.

## Decision replay + fork

Optimizer choices derive from scored numbers, so they replay: on resume the optimizer rescores under the
current scorer and re-runs each recorded decision — match → the round stands, mismatch → halt at the
divergence point, then a fork ([`../operations/persistence-and-state.md`](../operations/persistence-and-state.md)
§ Recovery: resume, rewind, fork). Decision records are **replayable** (which candidate won, the parameters
that gated it) vs **archival** (full LLM outputs, never read by replay).

## Composite — recorded, not gating

Round-winner selection compares candidates on difficulty-adjusted ability — θ on the tenant's δ ruler, less
the parent line's rows, so candidates scored on different adaptive subsets stay comparable
([`../methods/verdict-resolution.md`](../methods/verdict-resolution.md) § The model — Rasch θ/δ). **θ is fit
on the composite, not on accuracy**: each cell carries what it was WORTH (`objective`) beside whether it was
RIGHT (`fitness`), so a campaign that prices latency or cost elects on that price; a cost penalty folded into
`fitness` would render a correct-but-slow answer a MISS and send L1 to repair reasoning that was right.
Declare one under `{dataset}/dataset.yaml::campaign.scoring.per_cell`; absent, the two are one number.

**A miss is charged its cost too.** Each cell's `objective` is `(1 − s)·per_cell(row) + s·per_cell(row with
fitness 1)`, `s` = `formula/compiler.py::MISS_COST_SHARE`: a solved cell scores its composite, a miss keeps
`s` of what the same cell would have scored solved. The share enters the scorer id, as do the campaign's
judges. A charged error is a miss at the floor; an episode that ran out of turns and was graded is a scored
miss.

**A length charge is BOUNDED, so accuracy dominates it.** Every shipped token-charged template declares
`fitness * ((1 − λ) + λ · B / max(B, tokens))`, `λ = 0.08`, `B` the origin's median tokens: 1.0 up to `B`,
never below `1 − λ`. The guarantee: **no miss beats a hit**, and **no accuracy gap above `λ / (1 − s)` can be
overturned by length** — per arm the mean composite lies in `[(1 − λ)·E(a), E(a)]`, `E(a) = s + (1 − s)·a`.

**That shape is declared as DIALS, and the anchor is measured.** `scoring.dials: tokens=0.08,latency=0.05`
beside `per_sample` (exclusive with `per_cell`) names a weight per term; once the origin is scored,
`application/origin.py::lock_criterion` realizes it — `B` is the origin's median of that term — and writes the
resulting `per_cell` into the campaign manifest in place of the dials, once: a resume, a fork and every served
read take the plain formula. The shape and its inverse are `domain/scoring.py::anchored_criterion` /
`anchored_criterion_dials`. **Accuracy** displays alongside as the subset-relative correctness rate, so a win
with hidden costs is visible as a gap between the two.
