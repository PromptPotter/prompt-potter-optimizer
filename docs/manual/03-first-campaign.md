# Your first campaign

You run PromptPotter through [Claude Code](https://claude.com/claude-code), which ships with a skill called `/potter-run` that walks you through the whole workflow.

## Launch the skill

Open Claude Code (CLI, desktop app, or IDE extension) in the repo root, then type:

```
/potter-run
```

That's it. The skill takes over from here.

## What the skill does

1. **Checks your setup.** Your `.env`, your backend, and any run already in flight.
2. **Asks about the dataset.** Pass a name (`/potter-run justlogic/d234`) or let the skill show you what's available. Each dataset has its own config and starting prompt.
3. **Launches the campaign.** `python -m promptpotter new <name>` for a fresh one, `python -m promptpotter resume` to continue. Campaigns take minutes to hours.
4. **Watches and explains.** It reads each round as it lands and summarizes it — what won, what failed, what the critique said, what's next.

## What if I don't have a backend?

You'll need one: a dataset's calls run on a backend unless it names another connector. The default is the companion project [TermNorm-excel](https://github.com/runfish5/TermNorm-excel), and `/potter-run` clones and starts it beside this checkout. What a backend must expose: [`operations/backend-integration.md`](../operations/backend-integration.md).

## What if I don't want to use Claude Code?

Drive everything from the CLI: `python -m promptpotter --help` lists every verb. The skill is the recommended path because it handles the boring parts: dataset discovery, resume vs new, error triage.

Next: [Reading the output](04-reading-the-output.md).
