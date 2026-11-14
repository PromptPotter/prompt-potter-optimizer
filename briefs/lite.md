# lite: every data-root file read through its model; core's own word for a rate's lister (Opus, cloud)

Read `briefs/common.md` on `origin/brief/wave-5` whole first; it binds you. Chain id `lite`, arc `arc/lite`.

- **Base:** `origin/main` at `092418bd6`, main's tip with lw, rn-conn and rec landed. Your `<base sha>` in common.md is this commit.
- **The code shrinks**, judged as common.md says. Expect it to: a model replaces the `.get` walks and `isinstance` guards. Run `git diff --shortstat <base> HEAD -- promptpotter` after each link.
- **Spec:** the ledger section `### batch/29 · lite`, its one row ("Three data-root rows are proven by presence alone"). Re-run its re-test first.

## Decisions (orchestrator; build them, do not reopen them)

- **One model per file, named by its `store/data_root.py` row,** and `_WALKED` goes.
  - `rates.json`, both the bundled floor `llm/data/rates.json` and the fetched cache at `pricing.py::cache_path`: `models` maps a name to one rate body.
  - The operator's capability override (`capabilities.py::CAPABILITY_FILE`): per model, `reasoning_efforts` and `note`.
  - The capabilities cache (`CAPABILITIES_CACHE`): `fetched_at` plus `models`, each holding the fields `capabilities.py`'s fetch writes.
- **Each reader parses once, through the model.** The `.get` walks, the `isinstance` guards and `_as_int` / `_optional_cost`-style coercions over these files go where the model types the field.
- **Core's word is `lister`.**
  - Upstream's `litellm_provider` is translated in `pricing.py::_strip_upstream` alone, the one door upstream's vocabulary reaches.
  - The cache and the bundled floor hold `lister`.
  - The bundled floor is rewritten through that same strip, never by hand.
  - `git grep -c litellm_provider -- promptpotter` names only the strip.
- **A file that does not parse:**
  - the fetched rates are a CACHE: re-fetched, falling back to the floor;
  - the capabilities cache is RUN data, so it is re-fetched;
  - the operator's override is authored, so it refuses typed and names the file and the key.

  Its shape stays the same: typed, not reshaped.
- **Landing key.** The rows' schemas are hashed (the SURVIVOR and CACHE ones). Name the move and what it wipes.

## Links (one commit each, in order)

1. The three models and their rows; the readers through them; `lister`.

## Beside you

Name every shared file in your commit body.

- **la** (cloud) moves the LLM wire types into `llm/base.py` and may re-point imports in `pricing.py` or `capabilities.py`. Touch only your hunks.
- **readings** (cloud) and **webb-5a** (cloud) touch none of your files.

## Proof (beyond common.md)

- Equals-main UNMOVED on every leg, and the behaviour keys unchanged: a rate or a card is read the same.
- A malformed override names its file and key in the refusal.
- The row's re-test before and after: `git grep -c "_WALKED" -- promptpotter/infrastructure/store/data_root.py` is 0.
