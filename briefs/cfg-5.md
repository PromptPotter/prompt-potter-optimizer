# cfg-5: the factory default the import check cannot see (cloud)

`briefs/common.md` binds you (read BASE FIRST), with ONE override: the base is **`origin/land/w5i`
(the sha the dispatch names)**, the landing stack main will take, not origin/main; it carries cfg-3
(every knob defaults in `defaults.yaml`) and cfg-4 (`bench_defaults_pin` is `defaults.yaml`'s hash).
Push to `arc/cfg-5`. Your row: `docs/specs/code-debt-cleanup.md` § `wave-5 · cfg (what cfg-3 and
cfg-4 left at their landing)`, ONLY **The code-default refusal cannot see a factory default** (read
its full text). One link. Runs beside `webapp-1` (owns `webapp/`); do not touch it, and leave the
row above yours (the run declaration's merge) alone.

## The row
The import check in `domain/campaign_config.py` is the one walk of the config tree's defaults; make
it see a `default_factory`'s value too, so a factory yielding anything but an absence (`None`, `[]`,
`{}`) refuses at import exactly as a plain code default does. No second walker anywhere: if reading
the factories means walking `model_fields`, that walk REPLACES the schema read, and the message stays
one. **Re-test:** a fact that a sub-model field `Field(default_factory=lambda: [1])` raises at import
(build the model in the fact, do not edit shipped models), and the shipped tree still imports.

## Proof
Equals-main per `common.md` against the base (UNMOVED; any digest move named old → new). Full gate
green. Report on `out/cfg-5-report` (Links, Re-tests, Proof, Decided in-run, Rows closed, Rows to
file, Symptoms).
