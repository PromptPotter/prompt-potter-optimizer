# Model screens — `reactome-typeql-42`

Every model comparison run on this dataset, with its bill and the hour it was read. How to turn a screen into a choice of model is general and lives in [`docs/operations/dataset-reasoning-matrix.md`](../../docs/operations/dataset-reasoning-matrix.md) § Pick the model for what the run does; that file's top table names the model this dataset runs now.

## Which model for which run here

`mimo` pinned for a search on one stable prompt, `deepseek` on `relace` where the prompt changes every arm and accuracy near `mimo`'s is enough, `glm` where the question is whether a removal hurts.

## Screen: `reactome-typeql-42`, twelve questions

Measured 2026-10-04: the origin prompt, questions 0, 2, 4–8 and 11–15 (easy 2, medium 5, expert 4, recursion 1), three runs each, two retries, two cells in flight. Column meanings:
- **First / After 2:** accuracy on the first attempt and after two retries, the campaign's fitness.
- **Median s:** median wall clock of a question's three runs.
- **$/question:** *billed* is the OpenRouter key's usage read before and after the run; *list* is the tokens the harness reported times the listed price, and is a floor wherever no host was pinned.

| Model | Effort | Host | First | After 2 | Median s | $/question | Verdict |
|---|---|---|---|---|---|---|---|
| `deepseek/deepseek-v4.1-flash:nitro` | `none` | nitro | 0.64 | 0.72 | 7 | 0.0032 billed | **This screen's pick,** replaced by pinned `mimo` on price (§ One run per question, three models). Nearly the fastest, at 1.7× the cheapest host. |
| `z-ai/glm-5.3-flash` | `low` | unpinned | 0.78 | 0.81 | 17 | 0.0011 billed | **Runner-up.** The most accurate here and the cheapest on the bill, an eighth of its list price, so the prompt was cached. Passed over for speed and for headroom: it leaves the search less to find. |
| `deepseek/deepseek-v4.1-flash` | `none` | relace | 0.64 | 0.67 | 20 | 0.0019 billed | The fallback if the nitro route drifts onto a dearer host. |
| `deepseek/deepseek-v4.1-flash` | `none` | unpinned | 0.64 | 0.69 | 4 | 0.0019 list | Same model, host unknown. |
| `inclusionai/ling-3.0-flash` | default | unpinned | 0.61 | 0.69 | 37 | 0.0032 list | As accurate, five times slower: it writes 24k output tokens per question. |
| `xiaomi/mimo-v2.6-flash` | `none` | unpinned | 0.50 | 0.61 | 14 | 0.0008 billed, read early | The bill is an under-read: see § The bill, re-read. Less accurate. |
| `qwen/qwen3.8-27b:free` | `none` | unpinned | 0.50 | 0.58 | 14 | 0 | Measured 2026-10-05. Free and as quick as `mimo`, under the free tier's 20 requests a minute and 1,000 a day; a 28-question pass is over 100 requests. |
| `inception/mercury-2.5` | default | unpinned | 0.56 | 0.58 | 30 | 0.0050 list | Retries barely help it. |
| `qwen/qwen3.8-flash` | `none` | unpinned | 0.56 | 0.58 | 14 | 0.0118 list | Its input price makes it the dearest here. |
| `nex-agi/nex-n2.5-mini` | default | unpinned | 0.45 | 0.52 | 131 | 0.0084 list | Slow. Eleven questions: one of its queries exhausted the database's memory on every run. |
| `nvidia/nemotron-3.5-lightning` | default | unpinned | 0.25 | 0.44 | 121 | 0.0127 list | Slow, and weakest on the first attempt. |
| `qwen/qwen3.7-flash` | `none` | unpinned | 0.33 | 0.36 | 13 | 0.0030 list | Fast and cheap, and inaccurate. |
| `qwen/qwen3.5-35b-a3b` | `none` | unpinned | 0.33 | 0.39 | 35 | 0.0074 billed | Slow and inaccurate. |
| `qwen/qwen3.5-9b` | `none` | unpinned | 0.22 | 0.28 | 29 | 0.0134 billed | The least accurate, and the dearest on the bill: its misses re-send the prompt. |

`typesafe/jev-1.13` is not an id OpenRouter's API serves; `typesafe/jev-router` is a router with no fixed price and was not screened. `openai/gpt-oss-120b` at `low` ran the full origin pass rather than this screen: 0.38 on the first attempt over the 28 search questions, at about 17 s per question.

Twelve questions of three runs each put one question at 0.08 on either accuracy column, so the three `deepseek` rows are one reading and the ranking below `ling` is coarse. OpenRouter serves no 01.AI model, and DeepSeek V4 Pro and Claude Sonnet 5, the published rows' models, were left out on price. The bars are a screen's: they choose a development model and are no finding about any of these models.

## The bill, re-read

The key's usage counter lags the calls by minutes: after a run it went on rising for over two minutes. Every *billed* figure above was read 25 seconds after its run and is a floor, except `glm-5.3-flash`, which was re-read later. A bill is read only once it has held still for 90 seconds.

`xiaomi/mimo-v2.6-flash` at `none`, re-measured on 2026-10-05 with a settled bill. Its list price is $0.14 per M input tokens and its cached price $0.0028:

| Run | Local | UTC | Beijing | Host | Input tokens | Billed | $ per M input | $/question |
|---|---|---|---|---|---|---|---|---|
| Screen, 12 questions (2026-10-04) | 22:58 | 20:58 | 04:58 | unpinned | 800k | 0.0099, read early | 0.012 | 0.0008 |
| Campaign origin, 14 questions | 09:30 | 07:30 | 15:30 | unpinned | 941k | 0.0280 | 0.030 | 0.0020 |
| Screen, 6 easy questions | 09:42 | 07:42 | 15:42 | `xiaomi` | 278k | 0.0054 | 0.019 | 0.0009 |

- **A pinned host is what makes the cache pay.** Every call re-sends one 16k-token prefix. Pinned, about 88% of the input billed at the cached price; unpinned, about 80%, because the calls spread over seven hosts and each starts cold.
- **Whether the hour moves the price is not established.** The low reading fell in the Chinese night and the high one in the afternoon, but the low one was also the early read, and no host of this model lists a time-of-day price. Record the hour with every bill until two settled readings at different hours exist.
- **`inclusionai/ling-3.1-flash`, listed at zero, is too slow to use:** about 3 of 12 questions in ten minutes at 09:43 local, and stopped.

How far each per-question bill at three runs can be trusted:

| Model and host | $/question, three runs | When read | Trust |
|---|---|---|---|
| `z-ai/glm-5.3-flash`, unpinned | 0.0011 | 2026-10-04, re-read later | Fair. It is below what the price list predicts, so part of it is unexplained. |
| `deepseek/deepseek-v4.1-flash` on `relace` | 0.0019 | 2026-10-04, 25 s after the run | Good. It matches the price-list arithmetic. |
| `xiaomi/mimo-v2.6-flash`, unpinned | 0.0020 | 2026-10-05, settled | Good. |
| `xiaomi/mimo-v2.6-flash` on `xiaomi` | 0.0009 | 2026-10-05, settled | Fair. Easy questions only. |

With no cache discount at all, `mimo` at its list price is about $0.009 per question.

## One run per question, three models

The same twelve questions at ONE run each and two retries, on the search image (`dataset.md` § Our cut), each bill settled. Measured 2026-10-05 between 10:07 and 10:17 local (08:07 to 08:17 UTC, 16:07 to 16:17 Beijing):

| Model | Effort | Host | First attempt | After two retries | s/question | Billed | $/question | Where the bill goes |
|---|---|---|---|---|---|---|---|---|
| `xiaomi/mimo-v2.6-flash` | `none` | `xiaomi` | 0.583 | 0.583 | 12 | 0.0038 | 0.0003 | Input, about 97% of it at the cached price. |
| `deepseek/deepseek-v4.1-flash` | `none` | `relace` | 0.583 | 0.667 | 6 | 0.0075 | 0.0006 | Output at $2.40 per M; input is $0.003 per M with or without a cache. |
| `z-ai/glm-5.3-flash` | `low` | `relace` | 0.833 | 0.917 | 7 | 0.0096 | 0.0008 | Input, with no cache discount. |

- **`mimo` pinned is the cheapest, and the least accurate.** Its price rests wholly on the cache, so it holds only while the route stays on `xiaomi`.
- **`glm` leaves the least headroom.** One miss in twelve cannot separate two prompts; it is the model for a reading that must be sharp, not for a search that needs room to climb.
- **Twelve questions at one run is one draw per question.** A gap of one question between two models is inside it.
