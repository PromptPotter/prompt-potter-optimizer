# Your first campaign

You don't run PromptPotter from the command line directly. You run it through [Claude Code](https://claude.com/claude-code), which ships with a skill called `/potter-run` that walks you through the whole workflow.

## Launch the skill

Open Claude Code (CLI, desktop app, or IDE extension) in the repo root, then type:

```
/potter-run
```

That's it. The skill takes over from here.

## What the skill does

1. **Audits your setup.** Checks your `.env`, your backend, and any active sessions. If something's missing it tells you what and stops.
2. **Asks about the dataset.** You can pass a dataset name (`/potter-run bbeh`) or let the skill show you what's available. Datasets ship in `datasets/` — each one has its own config and starting prompt.
3. **Runs init.** Connects to the backend, loads the dataset, prepares the campaign directory. No backend calls yet — this is pure setup.
4. **Tells you to launch optimize.** Campaigns take minutes to hours, so the skill hands off to you: you open a terminal and run `python -m promptpotter optimize`. It runs in the foreground; you watch.
5. **Interprets the results.** When you come back to Claude Code, the skill reads the logs and summarizes each round — what won, what failed, what the critique said, what's next.

## What if I don't have a backend?

You'll need one. A backend is any service that exposes a `/matches` endpoint PromptPotter can send queries to. The simplest option is the companion project [TermNorm-excel](https://github.com/runfish5/TermNorm-excel). The skill will tell you if the backend isn't reachable.

For details on what a backend must expose, see [`operations/backend-integration.md`](../operations/backend-integration.md).

## What if I don't want to use Claude Code?

You can drive everything from the CLI directly — see [`operations/cli-reference.md`](../operations/cli-reference.md). But the skill is the recommended path because it handles the boring parts: dataset discovery, config selection, resume vs new, error triage.

Next: [Reading the output](04-reading-the-output.md).
