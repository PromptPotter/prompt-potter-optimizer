# Your first campaign

Run PromptPotter through [Claude Code](https://claude.com/claude-code) — it ships with a `/potter-run` skill that walks you through the workflow.

## Launch

Open Claude Code (CLI, desktop, or IDE extension) in the repo root, then:

```
/potter-run
```

That's it.

## What the skill does

1. **Audits setup** — checks `.env`, backend, active sessions. Stops if something's missing.
2. **Picks a dataset** — pass a name (`/potter-run bbeh`) or let the skill list available ones. Datasets ship in `datasets/`, each with its own config and starting prompt.
3. **Runs init** — connects to the backend, loads the dataset, prepares the campaign directory. No backend calls yet.
4. **Hands off to you** — campaigns take minutes to hours, so you open a terminal and run `python -m promptpotter resume`.
5. **Interprets results** — when you return to Claude Code, the skill reads logs and summarises each round.

## No backend?

`/potter-run` downloads and starts [TermNorm-excel](https://github.com/runfish5/TermNorm-excel) automatically. CLI-only: do it yourself, contract at [`operations/backend-integration.md`](../operations/backend-integration.md).

## No Claude Code?

Drive everything from the CLI: [`operations/cli-reference.md`](../operations/cli-reference.md). The skill is recommended because it handles dataset discovery, config selection, resume vs new, and error triage.

Next: [Reading the output](04-reading-the-output.md).
