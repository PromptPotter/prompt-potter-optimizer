# batch/1 review (67e823073, 5c7278ebd, ff04c4190 against 3cbd782e2)

**Verdict:** land after one fix pass. There is no transition code: no shim, alias or reader of two shapes. w2b is clean. w2a missed one FileSink output and left a switch that now reports a false state. w2d1 stopped two forwarders short of its brief.

## INTRODUCED

1. **`promptpotter/infrastructure/store/data_root.py:381`: the `prompt` row is dead, and its delta was not named.** FileSink also wrote `cycles/{id}/prompts/target_prompt/{version}/metadata.json` on every closed round with a head (`file_sink.py:245-246, 276-289` on main). Nothing else writes `prompts/`: on the tip, `git grep` for that path segment in `promptpotter/` hits only this row. The brief named only the `langfuse/` mirror, and its check (`find -path '*langfuse*'`) cannot see this directory.
   - **What is left behind:**
     - the `cycle prompt` and `draft prompt` rows match nothing;
     - `:323` describes Leaf.TRACE as "Loop telemetry: prompts, the Langfuse id map";
     - the same words sit in the `LEAF_INFO` mirror in `webapp/lib/api/types.generated.ts`;
     - `docs/operations/persistence-and-state.md:81` still lists `prompts/`.
   - **Failure:** someone looks for each round's winner prompt under `prompts/` (the storage panel's words, the persistence tree) and finds nothing. A later writer of `prompts/` inherits a row nobody designed.
   - **Invariant broken:** every `CAMPAIGN_ROWS` row names a file that something writes.
   - **What holds it:** delete the row, reword TRACE (it now holds the locks and `state.json`), regenerate the pair, and drop `prompts/` from `:81`. The charter admits no test. To confirm: in an offline home, `find <home> -path '*/prompts/*' -type f | wc -l` is greater than 0 on main and 0 on the tip.

2. **`promptpotter/application/views/ingress.py:93`: `obs_on=settings.OBS_ENABLED` now reports a state that is not true.** The value reaches `ansi.py:64` (the readout's `obs=ON`) and the events wire (`events-asyncapi.generated.json:3932`). On main, ON was true because FileSink wrote on every cycle. On the tip `OBS_ENABLED` defaults to True (`settings.py:202`), so a run with no Langfuse keys and no `MLFLOW_TRACKING_URI` builds zero sinks in `bridge.py:65` and still prints `obs=ON`.
   - **Failure:** the default box or offline run claims tracing is on. An operator who goes looking for the trace finds none anywhere.
   - `OBS_ENABLED` is also now a second switch over two sinks that each have their own: `LANGFUSE_ENABLED`/keys and `MLFLOW_TRACKING_URI`. `.env.example:53` still documents it as "File-based observability", the thing that was deleted.
   - **Invariant broken:** a served state equals the system's state.
   - **What holds it (largest excision):** delete `OBS_ENABLED` (`settings.py:202`, `bridge.py:50`, `.env.example:53-54`, `observability.md:3`; `settings_env` drops from 46 to 45). Derive `obs_on` from the cycle's projection having a sink. The charter admits no test.

3. **`promptpotter/infrastructure/backend.py:184, 187`: the `cell_attempts` and `cell_envelope_s` forwarders were kept, though the brief named both for deletion.** The forwarders are older than the batch; this one falls short of its brief.
   - Their only holder is `paper/bbeh-comparison/bbeh_potter_runner.py:165-166`. mypy checks that file (`pyproject.toml:264`), so a deletion would be caught.
   - The builder obeyed the out-of-Owns rule, but the result is the "kept the bridge, filed the root" shape.
   - **Fix pass:**
     - route those two paper lines through `client.connector.`;
     - delete both forwarders;
     - narrow the ledger row (`code-debt-cleanup.md:312-318`) to three members; its re-test count goes from 14 to 12.

## PRE-EXISTING (row texts)

- `application/maintenance/storage_report.py:31-34` — `_leaf_of` files any campaign file that no `CAMPAIGN_ROWS` row names under `trace`, and `campaign_store/store.py:131::_keepsake` drops it on `delete --keep-results`. The storage report never shows an unrow'd writer, and a row with no writer (finding 1) is never detected either. Action: report an unrow'd file as unclassified in the served answer, never fold it into a leaf. **Re-test:** `git grep -n "counts as loop trace" -- promptpotter`.
- `application/origin.py:75` reads `campaign_terms(session.backend_id)`, while `mask/load.py:216` now reads `campaign.connector`: two sources for one connector name. Add `origin.py:75`, `runner/inner/spawn_context.py:66` and `presentation/cli/commands/new.py:446` to the holders of the `Session.backend_id` row (`code-debt-cleanup.md:~303`).

## Outside Owns

- w2a lowered `scripts/complexity_ledger.py` (`any_params` 42→41, `p_record` 17→16). The gate forces this (`:528-536`), and the reason is in the commit body. Fine.
- w2b's `roadmap.md` M4b trim was already known.
- Every other edit is inside its arc's Owns.

## Stale lines for the fix pass

- `docs/specs/code-debt-cleanup.md:1139-1140`: "the langfuse mirror (`tracing/langfuse_sink.py`)" becomes "the Langfuse id map `langfuse/state.json` (`tracing/langfuse_sink.py`)".
- `docs/operations/persistence-and-state.md:81`: `prompts/` goes (finding 1). `langfuse/` exists only when Langfuse is configured.
- `data_root.py:323` and its `types.generated.ts` mirror: the TRACE words (finding 1).
- `.env.example:53` (known): the line goes with `OBS_ENABLED` (finding 2), or else rewords to "trace fan-out to Langfuse/MLflow".
- `pyproject.toml:139` (known): "why kept: per-call trace mirror (events.jsonl + cloud)" becomes "why kept: the Langfuse cloud sink".
- Root `CLAUDE.md`: no stale lines.
- Clean on the tip: repo-wide greps for `FileSink`, `file_sink`, `generate_observation_id`, `dataset_item_id(`, `Leaf.DATASET`, `backend_url` (Campaign), `reused_origin_id` and `derives_spend_bounds` across `paper/ scripts/ tests/ webapp/ docs/ .claude/ deploy-linux/`. Every remaining `.execution|.answer_key|.cell_payer|...` read is on a `Connector`.
