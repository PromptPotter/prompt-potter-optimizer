# faults-2b: a fault is a value; `logging` leaves application/ and domain/ (Opus, cloud)

Read `briefs/common.md` on `origin/brief/wave-5` whole first; it binds you. Chain id `faults-2b`, arc `arc/faults-2b`.

- **Base:** `origin/main` at `2d053c7db` (landed 86: faults-2a landed). Your `<base sha>` in common.md
  is this commit.
- **The code shrinks**, judged as common.md says: most of the 86 log calls go (at 2d053c7db: 87 in
  41 modules of `application/` + `domain/`). Run `git diff --shortstat <base> HEAD -- promptpotter`
  after each link.
- **Spec:** `### batch/28 · faults`, these rows only (re-run each Re-test first):
  - "Q3: one fault channel — a fault is a value";
  - "A `derived_file_stale` warning names the projection's class, never the file it could not write";
  - "A preflight warning reaches the terminal alone" (with `PreflightNote`'s facts);
  - "An origin gate's last error reaches a log alone";
  - "A handled parse failure prints two tracebacks";
  - faults-2a's five: "A loop refusal carries no code", "A refusal reads every bank to word itself",
    "Harbor's other refusals are `ValueError`s", "`new` spells its dataset twice", "A line file's kind
    is passed by its caller". Each is built as its row says, in link 1 or a fourth link before the ban.

## Decisions (orchestrator; build them, do not reopen them)

- **Every log in `application/` and `domain/` is one of three, decided per site and named:**
  deleted (no reader, or a typed record already holds its fact: verify the twin at the line);
  inside a run, a typed fault record through the run's one producer door (`append_record` /
  `CampaignStore.append`), read by a projection the readout, narration and dashboard already fold;
  inside a read, a field of the row it failed, served as its `Said`. No log moves to `infrastructure/`
  to dodge the ban.
- **The gate bans `import logging` in `application/` and `domain/`** as `gate.py::_IMPORTS_OPTIMIZER`
  bans an optimizer import, and lists every `except` there that neither raises nor hands its fault
  on: the list is empty at your tip (a deliberate swallow carries its reason as a declared row).
- **An ERROR log `webapp/e2e/serve.mjs` reads** (it fails a walk on one) goes only once its fault is a
  value the walk can read; say what replaced its signal.
- **A preflight warning is a narration row** every door reads (chat thread, dashboard, readout), and
  `PreflightNote.title` / `detail` are facts the renderer words.
- **The origin gate's stop carries its last error** as `Ended.detail` (the `OPTIMIZER_ABORT` channel);
  `runner/loop.py`'s `logger.error` goes.
- **`derived_file_stale` names the file**: each `Projected` declares the file it derives (a `derives`
  ClassVar), and the warning names it.
- **A handled fault is said once, as its sentence**; a traceback is printed only for a fault nobody
  handled (`shared/errors.py::graceful`).

## Links (one commit each, in order)

1. The fault record and its readers; the preflight row; the origin gate's detail; `derives`; the
   handled-fault sentence.
2. Every remaining log decided (deleted / record / served field), by module.
3. The gate's ban and the empty swallow list.

## Beside you

- **mcp** (cloud) owns `host_surface.py`, `presentation/mcp/`, `main.py`, `deps.py` and the mcp rows.
  Name every shared file in its link's commit body and touch only your hunk. Delete only your own rows
  in `code-debt-cleanup.md`.

## Proof (beyond common.md)

- Equals-main UNMOVED on every leg; behaviour keys unchanged. A leg whose `round_warnings` move names
  the record that now holds the fact.
- `git grep -l "^import logging" -- promptpotter/application promptpotter/domain` is empty.
- An `ORIGIN_GATE` readout names the origin's last error; a launch with a preflight warning shows it in
  the run's thread; a readout with a twice-failed critique holds no `Traceback`; a `dashboard.json`
  write fault names `dashboard.json`.
- `npm run e2e` (the gate's `playwright`) green: the walk still fails on a fault.
