# GSM8K — Dataset Context

Grade-school math word problems, OpenAI `openai/gsm8k` (~8.5K train / 1,319 test). Loader
`load_gsm8k`; answers carried in `#### N` form and extracted by `GSM8K_ANSWER_RE`.

Whether it is admitted as an optimization target, and the number that verdict rests on, is owned by
[`../../docs/research/benchmarks.md`](../../docs/research/benchmarks.md) § Every dataset we measured.

Knobs live in `campaign.yaml` and `pipeline.yaml`; the model pin is owned by
[`../../docs/operations/dataset-reasoning-matrix.md`](../../docs/operations/dataset-reasoning-matrix.md).
