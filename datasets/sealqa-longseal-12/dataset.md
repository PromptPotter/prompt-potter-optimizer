# sealqa-longseal-12 — SealQA LongSeal, 12-document haystack

> **On Windows, launch with `PYTHONUTF8=1`.** Harbor reads its own `task.toml`, `instruction.md`
> and trajectory files without naming an encoding, so a cp1252 default raises on any instruction
> carrying a curly quote or a non-Latin name — which is most of this haystack, and it raises before
> the container is built. `connectors/harbor.py::_preflight` refuses to start rather than let a
> round discover it one cell at a time.
>
> **Build before first mint:** `python scripts/build_sealqa_tasks.py sealqa-longseal-12`. It draws
> the cut at the pinned HF revision and writes the two artifacts this directory does not commit —
> `tasks/` (254 Harbor task directories) and `harbor_tasks.yaml` (the panel). Both are gitignored,
> the same split `datasets/*/cache.json` already makes: the config travels, the drawn rows do not.
> Here that is also a contamination guard — SealQA ships a per-row `canary` GUID precisely so
> leakage is detectable, and this repo is public.
>
> **This is the SINGLE-STEP config, and that is what it is for.** The published LongSeal numbers
> are for a single-turn long-context setting, so this cell carries the whole haystack in one
> episode's instruction and imposes no protocol on it. `sealqa-longseal-12-2step` is where a
> declared `retrieve -> answer` protocol and its per-step measurement live; this one is the anchor
> saying our instrument did not move the task, without which that config's per-step result has
> nothing to be read against.
>
> **No loader is registered for this name.** `wiring.py::_load_dataset_into_session` now reads a
> declared `experiment_file` FIRST, so rows cached under this name can no longer win — but they
> could, and did: a since-deleted `_load_longseal` left 12 MB of termnorm-shaped rows in
> `benchmark-rows/sealqa-longseal-12.json`, the panel was never published, and every cell of the
> first harbor launch raised while the run reported a healthy 254 samples. Delete stale rows under
> a harbor dataset's name anyway; they describe a different instrument.

## Data

**Source.** SealQA ([arXiv:2506.01062](https://arxiv.org/abs/2506.01062), ICLR 2026), released at
[`vtllms/sealqa`](https://huggingface.co/datasets/vtllms/sealqa). Three configs — `seal_0` (111
rows), `seal_hard` (254), `longseal` (254) — each a single `test` split. This dataset uses
`longseal`.

**Canonical protocol.** LongSeal extends SealQA to *"long-context, multi-document reasoning in
needle-in-a-haystack settings"*, and the published finding is that models *"still fail to reliably
identify relevant documents when faced with numerous distractors."* The haystack ships at three
sizes in the same row — `12_docs`, `20_docs`, `30_docs` — so **the size is the experimental
variable, not a sampling choice.** Answers are graded by SealQA's own auto-rater, which is
SimpleQA's grader minus three leniency rules plus a self-consistency rule; it is shipped here as
the `sealqa` judge.

**Our cut.** The full 254 rows at the **12-document** size — the smallest haystack, which is the
easiest of the three and the fastest per cell. Deviations, said out loud:

- **Size is part of the identity, not a parameter.** `sealqa-longseal-20` and `-30` are separate
  dataset directories, each naming its own `{12,20,30}_docs` column. Serving one size's rows under
  another's name would replay an easier reading for a harder question, silently — `sample_id` is
  scoped by dataset name and the query text is not in the key.
- **`urls` and `date` are dropped** when the haystack is written out. A URL is a retrieval artifact
  rather than evidence and `date` is frequently null, so both spend context the answer does not
  depend on. Documents are **numbered**, which is what lets a model cite one and a grounding judge
  read the citation back. `build_sealqa_tasks.py::_render_docs` is the one place that
  choice is made, on either layout — this config renders the haystack into the episode's
  instruction; the two-step config writes the same rendering one document per file.
- **The container has no way to reach the web, but on Docker Desktop that is NOT enforced** — and
  the difference is worth stating, because the first run's agent tried. It planned and ran
  `curl -s https://en.wikipedia.org/...`, got nothing (`ubuntu:24.04` ships no `curl`, `wget` or
  python, and the image adds only `tmux`/`asciinema`), and banked the empty pipeline's `0` as its
  answer. Harbor enforces `network_mode` through an egress sidecar needing `CONFIG_NFT_FIB_INET`
  in the daemon's kernel, which Docker Desktop's VM lacks, so declaring the policy ERRs every cell
  rather than sealing it. **On a Linux host that has it, add `network_mode = "no-network"` under
  `[agent]` and `[verifier]`** — that is where a run whose isolation must be guaranteed belongs.
  Meanwhile the task instruction states the machine is offline, which is what stops the agent
  spending an episode discovering it. This is the contamination axis SealQA's per-row `canary`
  exists to detect.
- **`golds` is carried by the dataset and not used.** It holds the gold documents themselves, in
  the same shape as the haystack. It could ground a *deterministic* retrieve check later; using it
  now would hand the answer's own evidence to the row the retrieve judge is meant to assess
  independently.

## Sample shape

One sample is one Harbor task, declared in the generated `harbor_tasks.yaml`. `query` is the
task **id**, so the task also declares `question` and `answer`: a judge reading `query` would grade
against an identifier, and `Sample.question` is the channel built for that
(`connectors/harbor.py::_extract_experiment`, which folds both into the instrument fingerprint).

**Labelled**, which matters and is a choice here rather than a given: harbor cells are normally
verifier-graded with no gold, and every `needs_gold` judge is skipped on such a bank. Declaring the
`answer` is what keeps `answer_correct` — SealQA's own published rater, and the term that makes our
number comparable to the paper's. The declaration is whole-bank or none; a mixed panel raises.

The 254 rows are fetched from `vtllms/sealqa` (config `longseal`, split `test`) to generate the
task directories — a build step, not a runtime loader. Needs the opt-in extra:
`pip install -e ".[benchmarks]"`, and running the campaign needs `.[harbor]` plus a container
runtime (`connectors/harbor.py::_preflight` fails naming both).

## Fine-grained scoring — the point of this dataset

The `retrieve → ground → answer` schema
([`../../promptpotter/judges/CLAUDE.md`](../../promptpotter/judges/CLAUDE.md) § The step schema),
banked as three named terms per cell:

| term | judge | asks |
|---|---|---|
| `evidence_settled` | `evidence_retrieval` | does the reasoning show evidence that SETTLES the question |
| `answer_grounded` | `answer_grounding` | is the answer traceable to the documents, or asserted |
| `answer_correct` | `sealqa` | is the answer right, under the paper's own auto-rater |

**Only `exact_match` decides the round.** All three judge terms are banked beside it and none is in
the scoring formula — a grading that fails past its retry omits its term and would bank a paid cell
as an ERROR, and two of the three rubrics are unscreened. This campaign exists partly to read them.

**The step terms may never become separate items.** They compose into one cell score that θ reads;
k steps per cell would claim kN observations where there are N, shrinking every SE by ~√k and
letting PoBB eliminate on confidence it never earned. Held by
`exploration.py::dedup_observations`; fitting δ and `a` per step needs a testlet model and a new
`ruler_id` ([`../../docs/methods/verdict-resolution.md`](../../docs/methods/verdict-resolution.md)
§ Phase 3).

## Caveats

1. **Two of the three rubrics are ours**, not upstream's, and unscreened. `sealqa` is the paper's.
2. **`evidence_settled` reads the model's own reasoning trace.** On a single-call backend that is
   whatever the model chose to write down, which is not the same as what it actually attended to. A
   terse correct answer may score MISSING. Read traces before trusting a low value.
3. **Some questions are unsettleable from their documents** by construction. `MISSING` is the
   correct reading there, not an instrument fault — which is why `evidence_settled` is banked
   rather than scored.
