# Display Conventions

Canonical visualization patterns that every PromptPotter entry point (notebook, CLI, API, webapp) should render identically. One pattern, learned once.

## The `⚠ … ↳` finding-and-addressed-by convention

PromptPotter surfaces optimizer findings — validation failures, anomaly flags, elimination signals, empty-output candidates, degradation escalations — with a two-line shape:

```
⚠ <what was found, in data terms>
  ↳ <what happens next, in optimizer terms>
```

Line 1 names the observation: *who, what, where*. Line 2 names the repair or consequence: *what the system will do about it*. A finding without a `↳` line is just noise; every ⚠ must be paired with an action.

### Canonical example — validation failure

```
⚠ llm_only.model = 'gpt-4o' ∉ [openai/gpt-oss-120b]
  ↳ scored 0; L2 directive will name this value
```

The first line is a structural fact about the candidate's `OptSearchPoint`. The second line tells the reader the signal has been absorbed by the feedback cycle — no human intervention required.

## Entry-point adoption

| Surface | Status | Location |
|---|---|---|
| Notebook | Implemented | `promptpotter/presentation/ui/campaign/display_callbacks.py` |
| CLI | Adopt for `show-status` / `show-results` | `promptpotter/presentation/cli/` |
| API | Return the `⚠ / ↳` pair as a structured pair in JSON so frontends render identically | `promptpotter/presentation/api/` |
| Webapp | M10+ | — |

When adding a new self-healing mechanism, escalation check, or any other finding the optimizer surfaces to the user, use this convention rather than inventing a new format.

## Anti-patterns

- Do not use ⚠ for log-level warnings unrelated to a SearchPoint.
- Do not omit the `↳` line.
- Do not render raw tracebacks or backend error bodies in the ⚠ slot — digest first.
- Do not stack multiple ⚠ lines without their own `↳` partners.

## Source of truth

`promptpotter/infrastructure/persistence/session_emitter.py` populates `dashboard.json.last_scoring_metadata` with the structured finding. Each entry point reads from there and formats using this convention — the data lives in one place, only the rendering is per-surface.
