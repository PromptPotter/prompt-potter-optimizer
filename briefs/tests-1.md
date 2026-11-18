# tests-1: the test world refuses an unrouted send; a factory cut renders (cloud)

`briefs/common.md` binds you. Base: `origin/main`. Rows are in `docs/specs/code-debt-cleanup.md`;
quote each row's title in the commit that closes it and delete it. One link per row, in this order;
a row that reads false at its line is reported, not built.

## Links
1. **A test that omits `offline` sends for real** (spend-4's report; three facts did until spend-4):
   the conftest world refuses every unrouted network send (`urlopen`, every `httpx` transport, name
   resolution) by default, so a fact that needs a network edge asks for `offline` (or the one
   fixture that routes it) and a fact missing it fails loudly instead of fetching. Reuse what
   `scripts/offline_world.py::install_network` already does; no second router. Name every fact the
   refusal turned red and what it was reaching. **Re-test:** the row's (a fact resolving an
   OpenRouter plan without `offline` raises), and the full suite green.
2. **`tests/factories.py::arm_state(cut="broken")` builds a reason no render reads** (the row's
   Action). **Re-test:** the row's.

## Owns
`tests/conftest.py`, `tests/factories.py`, the `offline`-fixture parameter of any test the refusal
turns red (signature only, in any module), `scripts/offline_world.py` only if the refusal must live
there (report it). NOT `promptpotter/` (a test that needs engine code changed to pass is reported),
and no other test body: the `owner-1` chain may add tests in modules of its choosing, so touch only
signatures there.

## Target
`promptpotter/` lines unchanged. Full gate green; `--against` legs and `--digests` unmoved.

## Report
Per `common.md`.
