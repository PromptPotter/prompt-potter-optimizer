# Operations

Four unrelated subjects share this directory. Load the group you need, not the folder.

## Running, watching, and diagnosing a campaign

| Page | Covers |
|------|--------|
| [Persistence and state](persistence-and-state.md) | The `.promptpotter/` tree, active session, cycle directory, `new` / `resume` flags, resume / rewind / fork / sweep, human-in-the-loop steer & fork + pause. **§ Diagnosing a live or stuck run** owns the triage order, the `RunPhase` vocabulary, why `declared_phase` is not `run_phase`, the heartbeat invariant and the `.runtime/` flags. Env vars live in [`../manual/02-install.md`](../manual/02-install.md#environment-variables) |
| [Observability](observability.md) | What gets traced, Langfuse integration, P(best) stream, display conventions |
| [Backend integration](backend-integration.md) | Contract a backend must implement (`/matches`, `/pipeline`, `/status`) and PromptPotter's REST API |

## Authoring a dataset

| Page | Covers |
|------|--------|
| [Dataset selection rationale](dataset-selection-rationale.md) | Why each dataset is or isn't wired, the trialed-and-rejected list, and **§ Adding a dataset** — the wiring process, canonical split first |
| [Dataset reasoning matrix](dataset-reasoning-matrix.md) | Per-dataset model + `reasoning_effort` + `max_tokens` defaults, output-ceiling traps, provider swap protocol |

Start at [`../../datasets/CLAUDE.md`](../../datasets/CLAUDE.md) — it routes to both.

## Security and deployment

| Page | Covers |
|------|--------|
| [Access model](access-model.md) | **The security map an audit opens** — the tier ladder and its boundary kinds, every enforcement point by symbol, the Linux-box deploy checklist, and **§ Running it securely** — blocking an account from Telegram via the on-box bot, the no-inbound-door rule, secret hygiene |
| [Linux deploy](../../deploy-linux/README.md) | systemd + Cloudflare Tunnel + OIDC + free-tier ceiling |

## Measurement carry-over

| Page | Covers |
|------|--------|
| [The mask](mask-projection.md) | How far a change to the criterion — a formula, a PoBB setting, the engine — reaches before the record stops carrying over, and where you have to fork instead. The `lens` params and the `ab` verb |

New to PromptPotter? Start in [`../manual/`](../manual/README.md).
