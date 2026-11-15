# mcp: a held task asks for its input; the host door over Streamable HTTP; a mount is a row (Opus, cloud)

Read `briefs/common.md` on `origin/brief/wave-5` whole first; it binds you. Chain id `mcp`, arc `arc/mcp`.

- **Base:** `origin/main` at `2d053c7db` (mkey landed: the bearer door exists; faults-2a landed: a refusal carries typed acts). Your `<base sha>` in common.md is this commit.
- **Spec:** the ledger section `### batch/29 · mcp`, every row. Re-run each row's Re-test at your base first.
- **The code may grow here**: link 2 adds a transport, and that is the substance. Every added line still answers
  common.md's question. Hunt deletions on the way: the bare `app.mount` and any second identity path. Report +/− per link.
- **Beside you:** **faults-2b** (cloud) owns `read_model.py`, `phases.py` and the logging rows. Disjoint from your
  files; a file you both need is named in your commit body and you touch only your hunk. Delete only your own ledger rows.

## The census (orchestrator, at main cf5050f67; re-grep it on your base)

- The held status: `application/command_reading.py::job_status` (`held` → `input_required`), `::_held`
  (`RunState.gate`); the arms gate is its one holder. The hold's words already exist as a refusal's acts:
  `domain/records/run_records.py` (`NextAct(kind="submit-arms", field="keep", …)`); the payload is
  `domain/command_kinds.py::SubmitArmsPayload`.
- The task's read: `application/host_surface.py::HostSession._task_result` (loops until TERMINAL, so a held
  task blocks `tasks/result` until the run ends), `::_open`, `::_cancel`; the handlers in
  `presentation/mcp/server.py::host_server` (`tasks/get`, `tasks/result`, `tasks/cancel`).
- The transport: `presentation/mcp/server.py::serve_stdio`, `::cmd_mcp` (the `mcp` CLI verb, the extra's guard).
  The installed SDK (`mcp` 2.3.0) ships `mcp/server/streamable_http_manager.py::StreamableHTTPSessionManager`
  and `server/session.py::ServerSession.elicit_form`.
- The mounts: `promptpotter/main.py::RouterMount`, `::API_ROUTERS`, `::_off_catalogue`, the import-time
  refusal loop, and the one bare `app.mount("/", StaticFiles(...))` (the catch-all, last).
- The caller: `presentation/api/deps.py::resolve_identity` (mkey's bearer lands here), `::open_caller`,
  `::_drawn`; the OIDC middleware sets `request.state.identity_ctx` before any mount runs.

## Decisions (orchestrator; build them, do not reopen them)

- **A held task asks.** On `tasks/result` for a task whose job holds at the arms gate, the session sends one
  form elicitation tied to the task (the MCP tasks spec's related-task metadata). Its schema is
  `SubmitArmsPayload`'s fields; its words come from the hold's own `next_acts`, one source.
  - An accepted answer is filed as the existing `submit-arms` command through the dispatcher at the host
    door, keyed by the task, so a retried answer acts once. No second filer, no host-only kind.
  - A decline or cancel leaves the task held and says so in the elicitation's result. `tasks/get` still reads
    `input_required`; `tasks/result` then waits on as it does now.
- **One adapter, two transports.** `host_server` is unchanged; Streamable HTTP serves it beside stdio.
  - Mounted on the API at one path. Each MCP session is bound to the caller its opening request resolved
    through `resolve_identity` (cookie or mkey's bearer, no third path). A later request on that session
    resolving to another account, or none, is refused.
  - The rate draw is the one `host_surface.py` already makes per request; the mount adds none.
  - DNS-rebinding protection reads the install's allowed origins (`settings.allowed_origins_list`).
  - The session manager runs in the API's lifespan, never at import.
- **A mount is a declared row.** `API_ROUTERS` holds an ASGI app beside an `APIRouter`, gated by the same
  `gate` field; the import-time refusal reads both. The webapp's `StaticFiles` becomes the last row. No bare
  `app.mount(` remains in `main.py`.
- **claude.ai's connector auth.** Read what a claude.ai custom connector sends (Anthropic's connector docs).
  - If it carries a bearer the box can accept, the re-test runs as the operator's step after the box update.
  - If it requires OAuth 2.1 (protected-resource metadata, an authorization server), do NOT build one. File
    a row naming the gap and its owner (`access-model.md` § The perimeter). This is a design fork for the
    operator.

## Links (one commit each, in order)

1. **The held task's input request:** `_task_result`'s elicitation, the `submit-arms` answer, and the tests.
2. **The HTTP transport and the mount row:** `API_ROUTERS` rows for ASGI apps, the MCP mount, `StaticFiles` as a
   row, `access-model.md` § The perimeter (the MCP door), and `stable-api.md`'s host section if one exists.

## Proof (beyond common.md)

- Equals-main UNMOVED on every leg: no run behaviour moves. Digests unmoved.
- Tests (in `tests/`, by `tests/CLAUDE.md`):
  - A host calling `tasks/result` on a held arms gate receives an elicitation. Its answer files `submit-arms`
    once (a retry acts once), and the task runs on.
  - Over Streamable HTTP with a bearer, `tools/list` answers. Without a carrier it is refused 401. With
    another account's bearer on an open session it is refused.
  - A host-account bearer is refused, as mkey's door does.
- `git grep -n "app.mount(" -- promptpotter/main.py` is empty.
- `playwright` green at the tip (the webapp still serves from its row).
