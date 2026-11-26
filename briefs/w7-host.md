# w7-host: the host posts a message to every account, read and dismissed at every door (cloud · Opus)

`briefs/common.md` and `briefs/w7-shared.md` bind you (read BASE FIRST). Base **`2c7dff34e`**.

SPEC: `docs/specs/roadmap.md` item **"Host messages, before the next deploy that wipes run data"**
(operator, 2026-11-26: the v0.8.21 landing deleted a new account's first result while it was being
read). This is a feature, so `promptpotter/` grows; its own substance stands, and every other added line
answers common.md's THE CODE SHRINKS.

DECIDED:
1. **One survivor file** holds the host's messages and each account's dismissals (keyed by the account's
   id), a new SURVIVOR row in `infrastructure/store/data_root.py` beside the accounts. No existing
   survivor's shape moves, so no migration ships; regenerate `data-root.generated.json`. Deleting an
   account drops its dismissals in the same write.
2. **A message** is: id, posted-at, text, and whether it pops up. One marked to pop up opens at sign-in
   and on the next page load until that account dismisses it; the rest wait in a list the account can
   open. A message is for every account (no audience field until someone needs one).
3. **Posted** from the CLI (a host verb, CLI-only carve-out) and from the admin bot
   (`presentation/admin_bot.py`), both through one `application/` command (a command-kind row).
   **Read and dismissed** at the REST API (the account's list, its dismiss), the webapp (a popup and a
   list), and the CLI (a read verb, so `/potter-run` and a no-browser device read it too).
   `application/embedded_run.py` is a named carve-out: no account at that door.
4. **The release skill** (`.claude/skills/potter-release/SKILL.md`, step 4) posts the wipe's warning
   before `update.sh` when the release wipes run data, and the release note after the box verifies.
   Print the exact CLI command in the skill. `deploy-linux/update.sh` changes only if the post must run
   on the box itself; say which in the report.
5. Name it: grep first so "message" never collides with the chat's or a send's messages; pick one
   self-describing name and use it in code, docs and the UI copy alike.

OWNS: new modules under `promptpotter/domain/`, `promptpotter/infrastructure/identity/` (beside
`accounts.py`), `promptpotter/application/`; `promptpotter/infrastructure/store/data_root.py`;
`promptpotter/presentation/admin_bot.py`; the CLI's write and read verb modules; a new REST router row in
`main.py::API_ROUTERS`; a new webapp component, its CSS and its mount in the shell; `webapp/e2e/walk/login.spec.ts`;
`tests/test_security.py` (a new block); `.claude/skills/potter-release/SKILL.md`; the docs that
name the survivors (`docs/operations/persistence-and-state.md`, `docs/operations/access-model.md`).

LINKS: (1) the survivor and the command, with tests; (2) API + CLI doors; (3) the webapp popup and
list, mobile first, walked by `login.spec.ts`; (4) the admin bot and the release skill.

PROOF: the full gate; equals-main must show no decision, request or digest move. In the report, list the
operator's steps to deploy it (it lands and deploys FIRST of wave 7) and the exact post command.
