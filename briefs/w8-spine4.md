# w8-spine4: the fitness fold moves down, so `.running` can go (cloud · Opus)

`briefs/common.md` and `briefs/w8-shared.md` bind you. w8-spine3 cut `.running`: its readers
(`domain/narration.py::Narration.fold`, `ledger_scan.py::StandingArm.fold`, `live_dashboard.py:683`)
need `metrics.py::compute_composite_fitness`, and that function reads `evaluators.py` in application,
which domain and infrastructure cannot import. Its report is on `origin/out/w8-spine3-report`.

OVERRIDE of w8-shared: this chain may move behaviour keys and digests, each named old→new and
explained by its mechanism.

SPEC: `docs/specs/code-debt-cleanup.md`. One commit per link:
1. The composite fitness is the domain's: move the fold (`compute_composite_fitness` and the part of
   `evaluators.py` it reads) down to `domain/`, so a domain reader folds a recorded row's fitness
   itself. Application keeps only what compiles a scorer.
2. **`QueryLoopState.running` re-folds what the recorder already holds**: its three readers read the
   recorder, and `running=` in `query_loop.py` goes from 3 to 0.
3. **`PairedReading` is a tagged union spelled as optional fields and a validator**.
4. **Fields written, never read: 27 left**: delete the ones the census proves unread, and mark the
   `_unread_fields` false positives spine3 found (`VerifySaturated.already_measured` is read by its
   sentence template).

OWNS: `promptpotter/application/scoring/metrics.py`, `application/scoring/evaluators.py` (the fold
only; the compile stays), `application/scoring/query_loop.py`, `domain/narration.py`,
`infrastructure/projections/ledger_scan.py`, `infrastructure/projections/live_dashboard.py`,
`domain/paired_reading.py`, the new domain module the fold lands in, `scripts/complexity_ledger.py`
(`_unread_fields` only), and their tests. Not yours: `domain/results.py`, `runner/round.py` and
`views/readout.py` (w8-round's). Each is a re-point only.

LEAN: `promptpotter/` shrinks. Run data: name each wipe.
