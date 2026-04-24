# Display Conventions

Per-query annotations render in this order, with a **mutual-exclusion rule**:

1. `⚠ {step}: {message}` — one line per diagnostic warning (always renders).
2. One status annotation from this exclusive set:
   - `🔄 cache had pipeline warnings → reran; result: …` — retried after cached degradation
   - `🔬 cache had warnings + rerun still degraded → resampled N fresh calls …` — samplescan rescue
   - `🔀 query degrades ≥50% of the time historically → using cached answer …` — switched out
   - `⚠ entire stale-data ladder exhausted → still degraded …` — persistently degraded
   - `↩ pipeline warning observed; X/Y occurrences toward rerun trigger …` — degraded observed, **AND** no fatal warning on this query

**Do not use the bare word "probe" here.** The stale-data ladder's rescue step is called "samplescan rescue" — "probe" is reserved for the L2/L3 **probe round** mechanism (round-scoped action targeting queries with recurring pipeline warnings), which is a completely different thing.

The fatal-warning suppression of `↩ …` is load-bearing: when a fatal warning fires, the candidate is dead on that query, so a counter reading "1/3 toward rerun" would falsely suggest more data is coming.

---

Canonical visualization patterns that every PromptPotter entry point (notebook, CLI, `/potter-run` skill, API, webapp) should render identically. One pattern, learned once.

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

The first line is a structural fact about the candidate's configuration. The second line tells the reader the signal has been absorbed by the feedback cycle — no human intervention required.

## Entry-point adoption

| Surface | Status | Location |
|---|---|---|
| Notebook | Implemented | `promptpotter/presentation/ui/campaign/display_callbacks.py` |
| CLI | Adopt for `show-status` / `show-results` | `promptpotter/presentation/cli/` |
| API | Return the `⚠ / ↳` pair as a structured pair in JSON so frontends render identically | `promptpotter/presentation/api/` |
| Webapp | Planned | — |

When adding a new self-healing mechanism, escalation check, or any other finding the optimizer surfaces to the user, use this convention rather than inventing a new format.

## Anti-patterns

- Do not omit the `↳` line.
- Do not render raw tracebacks or backend error bodies in the ⚠ slot — digest first.
- Do not stack multiple ⚠ lines without their own `↳` partners.

## Source of truth

`dashboard.json::last_scoring_metadata` holds the structured finding. Each entry point reads from there and formats using this convention — the data lives in one place, only the rendering is per-surface.
