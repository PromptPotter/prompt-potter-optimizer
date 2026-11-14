# rn-conn: one word for the connector, a typed registry refusal, `NodeCall` read by its fields (Opus, cloud)

Read `briefs/common.md` on `origin/brief/wave-5` whole first; it binds you. Chain id `rn-conn`, arc `arc/rn-conn`.

- **Base:** `origin/main` at `f589cbe9a`, main's tip with bkeys, faults-w and small-1 landed (`quota.py`, `draft_campaign.py`, `openai_compat.py` and `pipeline_resolve.py` are theirs first). Your `<base sha>` in common.md is this commit.
- **The code shrinks:** target ≤ −120 over `promptpotter/`. Run `git diff --shortstat <base> HEAD -- promptpotter` after each link.
- **Spec:** the ledger section `### batch/26 · rn-conn`, every row. The behaviour keys have landed, so the rows' "inside the re-key" and `PLUMBING_MODULES` clauses are stale. An edit to `Registry.get` re-keys nothing now. Cut those clauses in the commit that builds the row, and name the hunk.

## Decisions (orchestrator; build them, do not reopen them)

- **One word, `connector`.** `pipeline.backend_type`, `draft_backend_type` and `DraftPipeline.backend_type` become `connector`, the definition key included. Tenant definitions on disk are survivors' data: a one-shot migration under the landing rewrites the key (`docs/decisions.md` § 0011), and the shipped datasets are rewritten in the commit.
- **`Registry.get` refuses typed and names the registered set.** No `CONNECTORS.resolve` wrapper: that would be a second door. `_validate` refuses a non-lowercase name. Every caller takes the `Connector` the registry hands it, so the thirteen `CONNECTORS.get` sites read one answer.
- **A definition read's exceptions are named once,** beside `dataset_access.py::runnable_definition`. `admission.py::_definition_read`, `pipeline_resolve.py::_ROOT_UNRESOLVED` and `startability.py::_connector_extra` read that one set.
- **`NodeCall` is read by its fields** at every string read the row's re-test counts: 14 files, both connectors among them.
- **A model's context window comes off its card.**
  - One `LLMSpendBound` builder serves the three `_sent_spend_bound` hooks.
  - `max_input_tokens` leaves `harbor.py::_SEND_KEYS`.
  - A missing node value refuses at validation.
  - The `host-gateway` route moves into `docker_host.py::run_cell_container`.
  - The code defaults on node config (`db or "typedb"`, `examples or 0`, `environment or "docker"`, the default DB users and password) move into the shipped datasets' files: §0.5.

## Links (one commit each, in order)

1. `backend_type` → `connector` (with its migration), the typed registry refusal, the one exception set.
2. `NodeCall` read by its fields, the card's context window, the gateway in `docker_host.py`, the node defaults out of code.

## Beside you

Name every file you share with a chain running at your base in its link's commit body.

- **rec** (cloud, same base) makes the arm's act one record, turns state-fitting records into discriminated unions, deletes unread fields, and persists the cold fold snapshot. You share two files:
  - `store/data_root.py`: your migration row beside its CACHE row;
  - `scoring/sample_measurement.py`: your spend bound beside its cell beat in `_send_cell`.

  Touch only your rows' hunks there.
- **lw** (cloud) is webapp-only. `webapp/lib/api/types.generated.ts` is regenerated through its script, never by hand.

## Proof (beyond common.md)

- A dataset naming `connector: nope` answers 422 on `new`, on `start-run` and on the pipeline read.
- The migration is run over a copy of a tenant definition written at your base: the key moves, and nothing else does.
- Equals-main UNMOVED on every leg. The behaviour keys must not move; if one does, name what behaviour changed.
- Re-tests, each before and after:
  - `git grep -nw backend_type -- promptpotter` is empty;
  - the `.get("model"|…)` count is 0;
  - `max_input_tokens` is 0;
  - `host-gateway` is 1;
  - `cfg.get(…) or ` under `connectors/` is 0;
  - `_DB_DEFAULT_USERS` is 0.
