# Your first campaign

Run PromptPotter through [Claude Code](https://claude.com/claude-code) — it ships with a `/potter-run` skill that walks you through the workflow.

## Launch

Open Claude Code (CLI, desktop, or IDE extension) in the repo root, then:

```
/potter-run
```

That's it.

## What the skill does

1. **Reads where things stand** — the active run, its dashboard and readout; on a cold machine, `.env` and the backend first.
2. **Picks a dataset** — pass a name (`/potter-run justlogic/d234`) or let the skill list available ones. Your own datasets live under `.promptpotter/`; a few templates ship in `promptpotter/assets/datasets/`. Each has its own config and starting prompt.
3. **Launches** — `python -m promptpotter new <name>` for a fresh campaign, `resume` to continue one, in the background: a campaign takes minutes to hours. Whether it asks you first is the repo's autonomy mode — [`CLAUDE.md`](../../CLAUDE.md) § Working principles.
4. **Supervises and interprets** — reads each round as it lands and summarises it. The held-out bench score is graded on request: `python -m promptpotter bench`.

## No backend?

`/potter-run` clones [TermNorm-excel](https://github.com/runfish5/TermNorm-excel) beside this checkout and starts it. CLI-only: do it yourself, contract at [`operations/backend-integration.md`](../operations/backend-integration.md).

## No Claude Code?

Drive everything from the CLI — `new` / `resume` flags and state layout are in [`operations/persistence-and-state.md`](../operations/persistence-and-state.md#cli-flags--new-and-resume). The skill is recommended because it handles dataset discovery, config selection, resume vs new, and error triage.

Next: [Reading the output](04-reading-the-output.md).
