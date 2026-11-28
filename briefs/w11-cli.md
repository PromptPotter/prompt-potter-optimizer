# w11-cli: area 12, the terminal and server adapters: no business logic in an adapter (cloud · Opus)

`briefs/common.md`, `briefs/w10-shared.md` and `briefs/w11-shared.md` bind you. SPEC: `briefs/map.md`
§ 12 and the debt doc's `## 12` section. w11-web3's census read the five unexamined adapters and filed the
rows below. This chain closes them.

Target: the `## 12` Target line. An adapter hands data and renders; a renderer is `application/views/`'s; a
CLI verb is one `Verb` row; every write goes through `CommandDispatcher.dispatch`; everything on stdout is
also on disk.

Links, deletion first. Work each row along its root:
1. **The maintenance verbs' renderers live in the CLI adapter**: the four renderers move to
   `application/views/render`.
2. **The CLI's welcome and the archive modes are spelled beside their rows**: `ArchiveModeInfo` serves
   `irreversible`, and the webapp's `ArchiveCompactionControl.tsx` reads it.
3. **A refused Ctrl+C pause is swallowed**: both go through `send`.
4. **A terminal launch's start-up summary is said on stderr only**: the readout's header renders it. If
   the readout's writer is `run_observers.py` (w11-spend2's file), cut this link and file a line naming the
   writer.
5. **The server's adapter words health and shutdown itself**: `health.py` serves the row, and `notices.py`
   owns the notice.
6. **The admin bot keeps its own verb table, grammar and spend sums**: the verb and grammar half only. The
   spend sums move to `jobs/install_spend.py`, which is w11-spend2's file: leave that half and its line.
7. **The host door's import closure loads the LLM client**: `checkin_template` moves into
   `optimizer_manifest.py`, with no lazy import.

OWNS: `presentation/cli/**`, `presentation/admin_bot.py`, `promptpotter/main.py`, `application/health.py`,
`application/notices.py`, `application/views/**`, `application/host_surface.py`, `bench/task_context.py`'s
`checkin_template` and its move into `application/optimizer_manifest.py`, `domain/command_kinds.py`'s
`ArchiveModeInfo`, `webapp/components/account/ArchiveCompactionControl.tsx`, and the tests of these facts
(CLI, admin bot, host door).

Running at the same time:
- w11-spend2 owns area 05's files (`jobs/install_spend.py` included), `run_observers.py`, `hierarchy.py`
  and `served_dashboard.py`.
- w11-tests5 owns `scripts/` and the test harness (`conftest.py`, `factories.py`, `offline_world.py`).

DONE WHEN:
- Each row is re-tested by its grep, and the behaviour through its door, before and after.
- Equals-main names each move.
- `python scripts/gate.py` is green.
