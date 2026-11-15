# tv-2: the host's exhibit, served read-only to a follower (Opus, cloud)

Read `briefs/common.md` on `origin/brief/wave-5` whole first; it binds you. Chain id `tv-2`, arc `arc/tv-2`.

- **Base:** `origin/main` at `5273b6adb` (landed 87: mkey and mcp landed, the access review is one doc you extend). Your
  `<base sha>` in common.md is this commit.
- **Spec:** the ledger section `### batch/29 · tv-2`, every row, and its operator line (2026-11-11). Re-run
  each row's Re-test at your base first.
- **The code may grow here**: the one cross-tenant read is the substance. Every added line still answers
  common.md's question. Hunt deletions on the way: the "Try & learn" lede's promise, and any second
  follow rule. Report +/− per link.
- **Beside you:** **faults-2b** (cloud) owns `read_model.py`, `phases.py` and every `logging` call in
  `application/` and `domain/`. A file you both need (`cycle_addressing.py` is likely) is named in your
  commit body, and you touch only your hunk. Delete only your own ledger rows.

## The census (orchestrator, at main cf5050f67; re-grep it on your base)

- The follow pick: `application/cycle_addressing.py::followed_cycle` (running, else the latest launch),
  `::ActiveSessionResponse` (`tenant_id`, `hop`), `::active_session`; served by
  `read_params.py::ActiveSessionParams` (`/sessions/active`); read by `webapp/lib/api/reads.ts` and
  `webapp/lib/hooks/useRegistry.ts`; drawn by `webapp/app/stage/page.tsx`.
- The tenancy: `infrastructure/store/stores.py::Stores`, `::owned_campaign`,
  `presentation/api/deps.py::build_stores_from_identity`. Every hierarchy, lineage and dashboard read
  is scoped to the caller's tenant.
- The host: `infrastructure/identity/accounts.py::is_host_account`, `::host_user_id`, `::is_host`.
- The switch: `infrastructure/store/user_store.py` (`demo_mode_enabled`), `application/account.py::user_settings`,
  `commands/workspace_edits.py` (`set-user-settings`), `domain/command_kinds.py` (its payload), and
  `webapp/components/account/AccountPreferencesTab.tsx` ("Try & learn", whose lede promises a
  support-ticket dataset no code places).

## Decisions (orchestrator, from the operator's line; build them, do not reopen them)

- **One setting names the exhibit**: a campaign in the host account, by its id, in `promptpotter/config`.
  Unset means no exhibit.
- **The follow pick falls through**: a cycle the caller's run holds, else its latest launch, else the
  exhibit's newest cycle, else a `Said`. The exhibit step is taken only where the caller's
  `demo_mode_enabled` is on; that is its reader.
  - With no exhibit named, or the switch off, the response carries the `Said`, never an empty hop the
    browser words for itself.
  - `ActiveSessionResponse` says whose cycle it is (the caller's or the exhibit's), so the stage never
    guesses.
- **The one cross-tenant read.** Reads of the exhibit campaign's subjects (hierarchy, lineage, dashboard,
  rounds) resolve to the host tenant's stores, read-only, in `application/`.
  - One function decides it, keyed by the setting, so no router and no second tenancy path exists.
  - No command reaches it: the dispatcher's stores stay the caller's, so a command on the exhibit is the
    cross-user 404 it is now.
  - A blocked account reads nothing extra.
- **The switch's words say what it does**: "Try & learn" follows the host's exhibit while you have nothing
  running. The support-ticket promise goes.
- **The access review**: `docs/operations/access-model.md` gains the exhibit read in § The perimeter, with
  who sees it (a signed-in, unblocked account with the switch on), what it reads, and that it acts on
  nothing.

## Links (one commit each, in order)

1. **The setting and the read:** the setting, the one cross-tenant resolution, `access-model.md`.
2. **The follow pick and its switch:** the fall-through, `ActiveSessionResponse`'s whose and `Said`,
   `demo_mode_enabled` as the exhibit's reader, the tab's words, and `/stage/` drawing it.
3. **mcp's two report rows** (in `### batch/29 · mcp`, "Rides with: tv-2"): a held task on a 2026-07-28
   wire asked for its input through `tasks/update`, and the HTTP door's managers swept once their
   sessions end. Both live in `presentation/mcp/server.py` and `application/host_surface.py`; the
   access review in link 1 names the door's session binding beside the exhibit.

## Proof (beyond common.md)

- Equals-main UNMOVED on every leg: no run behaviour moves. Digests unmoved; a landing-key move is named
  with its wipe, and an account survives.
- Tests (in `tests/`, by `tests/CLAUDE.md`):
  - An account with nothing launched and the switch on follows the exhibit and draws its hierarchy.
  - Every command on the exhibit is refused. With the switch off it gets the `Said`.
  - With no exhibit named it gets the `Said`.
  - A blocked account reads no exhibit.
- Each row's re-test, before and after.
- `playwright` green at the tip, with a shot of `/stage/` drawing the exhibit at 390 px.
