# Code-Debt Cleanup — Backlog

Dump location for new debt as it's found. Add a bullet under **Active
backlog** with enough detail that a future session can pick it up cold:
- file + line range (or symbol)
- one sentence on *why* it's debt (not "what" — the code shows what)
- proposed action (delete / inline / extract / replace / verify)
- any blockers (needs telemetry, needs a mini-spec, depends on another
  item)

When an item ships, delete it from the file. The file is the live
backlog, not a history log — `git log` is the history layer.

## Active backlog

*(empty)*

## M13+ intentional UI placeholders

UI affordances the product *intentionally* ships disabled today — they
preview the M13+ chat-first UX + config-edit surface + analytics-search
surface. They are **not** scaffolding, not credibility hits, and not in
scope for any "hide non-functional controls" sweep.

| Placeholder | File | Future surface |
|---|---|---|
| Topbar search input (disabled) | `webapp/components/shell/Topbar.tsx:29` | M13+ analytics search |
| ChatPane attach + textarea + send button (disabled) | `webapp/components/dashboard/ChatPane.tsx:273-279` | M13+ chat-first operator UX |
| ChatPane Extended-thinking / Web-search / Code-execution toggles (`toggle locked`) | `webapp/components/dashboard/ChatPane.tsx:286-322` | M13+ chat-first feature toggles |
| ChatPane "job-footer" — "Adjust spend / finishing criteria — wired in M12" | `webapp/components/dashboard/ChatPane.tsx:204-206` | M12 control-plane (spend cap + finishing criteria editor) |
| ConfigMenu — gear icon + frozen-parameters panel | `webapp/components/dashboard/ConfigMenu.tsx` (+ render at `ChatPane.tsx:217`) | M12 control-plane (editable config surface) |

**Rule:** any future cleanup that touches these surfaces must
distinguish *intentional placeholder* from *scaffolding text/comment*.
Milestone-reference text inside these placeholders is OK (and exempts
them from a "no M-milestone references on operator surfaces" final-grep
gate); other operator surfaces still must not leak milestone numbers.

## History

All prior tiers + the pre-public-release polish arc (Tiers 0–6 + polish
A–E + audits 1–3) closed by 2026-05-25. Done-log entries lived here and
were pruned with the arc; recover via `git log` if needed.
