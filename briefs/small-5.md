# small-5: a Literal `--set` takes its bare word; a truncated call keeps its attempt (cloud)

`briefs/common.md` binds you. Base: `origin/main` (3784ed33b or later). No ledger rows exist yet:
these two paragraphs are the rows. One link each; file nothing you close.

## Links
1. **`--set` on a Literal field demands JSON quotes**: `--set
   'config.optimization.arm_gate="author"'` is the only spelling that reaches `author`; the bare
   `--set config.optimization.arm_gate=author` does not (re-test at the base first and quote the
   refusal or the wrong value). The CLI builds each argument from the field's declared spelling
   (`presentation/cli/main.py`, `--set`), so a value is parsed by the field's type: a Literal or
   enum takes the bare word, a JSON-shaped value still parses as JSON. Re-test: both spellings
   reach `author`; a word outside the Literal refuses naming the allowed set.
2. **A truncated optimizer call keeps only its healed re-ask**: `application/bench/llm_call.py`
   records the final `content`, and the first attempt's `finish=length` survives only as text inside
   `schema_repair_errors`. Its token split (reasoning vs visible) and its length are gone, so a
   reader cannot tell a runaway loop from reasoning that spent the `max_tokens` budget (seen
   three times on 2026-11-18: an inner `l1_critique` at 32k and 27k chars, an `l1_generate` at
   40.5k, max_tokens 16k/20k). Action: each attempt the repair ladder made carries its finish
   reason, its output and reasoning token counts and its char length, on the one record the call
   already writes; never its whole text. Grep every reader of `schema_repair_errors` first
   (`runner/inner/transcript.py`, `runner/output.py`, `domain/narration.py`, `domain/round_audit.py`).
   Re-test: an offline call whose first attempt ends `length` records that attempt's split.

## Owns
Link 1: `presentation/cli/main.py` and the module building `--set` values, their tests. Link 2:
`application/bench/llm_call.py`, `infrastructure/llm/base.py` (the response model's attempt field
only: the running `l4-valid` chain may touch error types there, so stay in your hunk), the repair
ladder module, the four readers above, their tests, regenerated specs/types.

## Target
Net ≤ 0 on link 1; link 2 grows by its own substance, named.

## Report
Per `common.md`.
