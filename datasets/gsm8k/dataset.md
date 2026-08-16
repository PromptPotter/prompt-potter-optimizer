# GSM8K — Dataset Context

Grade-school math word problems, OpenAI `openai/gsm8k` (~8.5K train / 1,319 test). Loader
`load_gsm8k`; answers carried in `#### N` form and extracted by `GSM8K_ANSWER_RE`.

**Saturated at the admission bar** (~78% for `gpt-oss-20b`), so it is kept for literature-citation
reproducibility, not as an optimization target — see
[`../../docs/research/benchmarks.md`](../../docs/research/benchmarks.md) § Every dataset we measured.

Knobs live in `campaign.yaml` and `pipeline.yaml`; the model pin is owned by
[`../../docs/operations/dataset-reasoning-matrix.md`](../../docs/operations/dataset-reasoning-matrix.md).
