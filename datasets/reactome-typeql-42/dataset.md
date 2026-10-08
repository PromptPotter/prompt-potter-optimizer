# reactome-typeql-42 — TypeDB's query-generation benchmark, TypeQL arm

> **Needs Docker, a built runner image and a loaded TypeDB.** § Standing it up has the three
> steps. The first cell fails loudly on whichever is missing: the harness validates its image
> assets and health-checks the database before it calls a model.
>
> **The model in `pipeline.yaml` is the DEVELOPMENT model.** The published rows are Claude Sonnet 5
> and DeepSeek V4 Pro. Nothing measured on another model sits beside them.

## Data

**Source.** [`typedb/db-llm-bench`](https://github.com/typedb/db-llm-bench) (Apache-2.0), pinned at
commit `7072e6e3e2863749b2a98b43225a61285e4c9a91`. The write-up is
[Benchmarking LLM query generation across SQL, Cypher and TypeQL](https://typedb.com/blog/benchmarking-llm-query-generation-across-sql-cypher-and-typeql).
The database is [Reactome](https://reactome.org/) release **97** (2026-06-17), loaded from
Reactome's own Neo4j dump and exported into TypeDB by upstream's `seed.sh`.

**Canonical protocol** (upstream `README.md`, `src/runner/src/lib.rs`). A model is given a prompt
template filled with the schema, optional examples and an optional query-language skill, plus the
question and one line stating the return shape its expected value implies. The last fenced code
block of the reply is executed read-only. A syntax error, an execution error, a timeout or a reply
with no query is fed back with the conversation for a corrected query, up to the retry budget. A
query that runs is final: its result is compared with the expected value and never retried. The
literal token `UNANSWERABLE` on its own line is final too, and correct only for the three
deliberately unanswerable questions. Each question runs **three times**. Accuracy is the share of
runs whose result matched, reported at 0, 2 and 4 retries — one execution at 4, the lower two cut
from its attempt trace.

**The panel.** `questions.json` is upstream's `data/reactome/questions.json`, byte for byte: 42
questions — easy 4, medium 5, expert 11, polymorphism 11, recursion 4, unanswerable 3,
aggregation 2, argmax 1, reification 1. There is no published train/test split; every published
number is over all 42.

**Published rows** (TypeQL arm, 42 questions × 3 runs, accuracy %):

| Model | no skill, no examples, 0 retries | skill + 5 examples, 0 retries | skill + 5 examples, 4 retries |
|---|---|---|---|
| Claude Sonnet 5 | 24.8 | 75.2 | 92.3 |
| DeepSeek V4 Pro | 0.0 | 62.4 | 85.5 |

The origin here is the middle column's configuration: their five instruction lines, their skill,
five examples.

## Our cut

- **All 42 questions, their harness, their scorer.** A cell is one question: the connector runs
  upstream's own binary on it (`connectors/dbllmbench.py`), so the retry loop, the result shaping
  and the comparison are theirs.
- **What varies is the head of their prompt.** The candidate replaces the five instruction lines
  and the skill. The template from its `{{skills}}` slot down is theirs and fixed.
- **Scored after two retries** (`accuracy_r2`). First-attempt accuracy, the visible-error and
  silently-wrong shares and the retries used are banked on every row and not scored. Two is the
  search's budget; a reported pass reads the protocol's 0, 2 and 4 (`retry_levels`).
- **One run per question while searching, three when reporting.** Upstream hardcodes three; the
  search image is upstream plus a `repetitions:` config key
  (`connectors/resources/dbllmbench-repetitions.patch`), so a search cell is NOT the published
  harness. A reported pass runs the unpatched image at three.
- **The search image also reports cached tokens** (`dbllmbench-cached-tokens.patch`). Upstream
  counts input and output alone, so a cell on its own build is priced with every input token at
  the list rate: the ledger of a reported pass is a ceiling on its bill, never the bill.
- **14 questions are held out as the bench set** (`campaign.yaml::dataset_split`), ranked by
  content hash. That is our split, not theirs. A number comparable with the published rows is a
  pass over all 42, and it must say that 28 of them were searched on.
- **`problem_description` holds their skill with trailing whitespace stripped** from four lines,
  which YAML block scalars cannot carry. Nothing else in the origin differs from their prompt.

## Standing it up

Everything upstream — the clone, the 528 MB dump, the exported CSVs — lives outside this
directory. Clone with LF line endings; upstream's `.gitattributes` says why.

1. **The runner images**, once per upstream commit. The search image is the one `pipeline.yaml`
   names; the unpatched one runs `verify` and a reported pass:

   ```sh
   docker build -f promptpotter/connectors/resources/dbllmbench.Dockerfile \
       --build-arg DB_LLM_BENCH_COMMIT=7072e6e3e2863749b2a98b43225a61285e4c9a91 \
       --build-arg DB_LLM_BENCH_PATCHES="dbllmbench-repetitions.patch dbllmbench-cached-tokens.patch" \
       -t dbllmbench:7072e6e3e286-search promptpotter/connectors/resources
   docker build -f promptpotter/connectors/resources/dbllmbench.Dockerfile \
       --build-arg DB_LLM_BENCH_COMMIT=7072e6e3e2863749b2a98b43225a61285e4c9a91 \
       -t dbllmbench:7072e6e3e286 promptpotter/connectors/resources
   ```

   A cell refuses an image that is missing, or whose build commit is not the one its tag names.

2. **The database.** Fetch `https://reactome.org/download/97/reactome.graphdb.dump` into
   upstream's `data/reactome/neo4j/` and follow upstream's `databases/reactome/README.md`. MySQL is
   not needed. **Fetch release 97 by number, never `current`: a new release moves the expected
   values** — the dump this dataset was built on has SHA-256
   `8b0eb24f1418edcadb345082502283db89ef22e552edd49cdec29b320585fabc`. On a machine with under
   ~12 GB for containers, run the export and the load one after the other: Neo4j serves only the
   export, so stop it before TypeDB bulk-loads. Under an 8 GB WSL2 cap the Neo4j export runs with
   heap 3G, pagecache 1G and `db_transaction_timeout 1800s`. A local TypeDB serves without TLS, so
   upstream's `load.sh` needs `--tls-disabled` on both the loader and the console.

3. **Where the database is**, if not upstream's compose stack on this machine: the four
   `DBLLMBENCH_DB_*` keys in `.env`, beside the API keys (`config/settings.py`). A TypeDB Cloud
   cluster is its gRPC `host:port`, the admin password and `DBLLMBENCH_DB_TLS=true`. Loading it is
   upstream's `load.sh` with `ADDRESS`/`DB_PASS` set and a loader whose version equals the
   cluster's — TLS is the loader's default, so the exported CSVs reach the cloud with no Neo4j and
   no local TypeDB running. The cloud needs TypeDB ≥ 3.12, and the version on the cluster page is
   not the server's: probe `https://<host>:80/v1/version` (a 404 means the server is down). Load
   with `PARALLEL=1` — at 2 the cluster rejects writes as isolation conflicts. A 4 GB node
   (e2-medium) dies on the R-HSA-168256 recursive traversal.

**The fidelity check** is upstream's own `verify` binary, run through the same image: it executes
every reference query and compares the result with `expected`. Run it after every load and before
every campaign — a store it does not pass grades every cell against answers it does not hold.
Its config sends no model: `models: [dummy: {}]`, `exampleCounts: [0]`, `maxRetryCounts: [0]`.
