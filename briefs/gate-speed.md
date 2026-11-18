# gate-speed: a timed start never runs beside the gate's CPU-heavy checks (Opus, cloud)

Read `briefs/common.md` on `origin/brief/wave-5` whole first; it binds you. Chain id `gate-speed`, arc
`arc/gate-speed`.

- **Base:** `origin/main` (fetch it; at least `7d5d12f7b`). Your `<base sha>` is the commit you branch
  from.
- **Spec:** `docs/specs/code-debt-cleanup.md` § `batch/32 · rel`, two rows only (re-run each Re-test
  first): "The dev loop still serializes on Vitest" and "Playwright's server starts beside the gate's
  heaviest checks". **Not yours:** the Windows replace row and the long-path row (Windows-only, they
  run local and last).
- **Why now:** six chains and a pilot share one desk today; every chain's push gate is the slowest
  step of its landing, and a timed start that trips on load costs a whole re-run (11–15 min).
- **The root the rows name:** `scripts/gate.py` admits a check by memory alone, so a check with a
  hard-coded start timeout (Playwright's `webServer` 120 s in `webapp/playwright.config.ts`, Vitest's
  90 s worker start) runs beside mypy, pytest and offline-run and trips on CPU, not on a defect.
  Decide the admission by what the check needs (CPU as well as memory, or a timed-start class the
  gate runs apart from the CPU-bound ones), never by raising a timeout alone (a timeout is a rail,
  not the fix; root `CLAUDE.md`). Keep the whole gate's wall clock no worse: report it before and
  after on your box (`gate.py` prints it), and the per-check times.
- **The code shrinks** where it can; the admission rule is substance.
- **Beside you:** **l4-runs** (cloud) owns `scripts/processes.py::clone_root` (a hunk you must not
  touch; name the file if you need another of its hunks); nobody else touches `scripts/gate.py`,
  `webapp/playwright.config.ts` or `webapp/vitest.config.ts`. You own those and the gate's docs
  (`docs/developer/` gate pages).
- **Proof (beyond common.md):** the full gate under an artificial CPU load you start beside it
  (e.g. `stress`-free: N busy python loops, N = cores), Playwright and Vitest green, twice; the same
  run on the base trips (or say it does not reproduce on your box, with its numbers).
