# Decisions

Each entry is a boundary, why it sits there, and the symbol that enforces it; what has shipped is the code's and [`specs/roadmap.md`](specs/roadmap.md)'s. Paths are under `promptpotter/` unless they start with `scripts/`.

## 0001 — M12 control plane

- **The wire is declared, never handwritten.** Payload and record models generate the OpenAPI (inbound) and AsyncAPI (outbound) specs (`scripts/build_specs.py`), and a command or event kind declares its model before its handler lands. The closed sets are `domain/command_kinds.py::CommandKind` and `domain/run_records.py::CycleRecord`. Without a declared wire every screen invents an endpoint shape and drift is invisible.
- **Commands are ledger records, never a second pipeline.** `application/commands/dispatcher.py::CommandDispatcher` writes every `CommandRecord`, whichever door sent it. It acks `rejected` at dispatch, `accepted` when only a running loop can take the kind, and `applied` only when the effect happened, written by the loop at the checkpoint that takes it (`application/run_phase_control.py::RunControl`). A client believes only acks.
- **Outbound frames have no writer.** SSE tails the on-disk ledger (`infrastructure/projections/event_stream.py::CycleLedgerTail`) as `domain/projection_envelope.py::ProjectionEnvelope`, so any process may write and any reader may subscribe. Identity scope rides the ledger's path, never a per-record `tenant_id`.
- **A served GET is not a command and adds no I/O kind.** It rides its resource's own router, is declared before its handler, resolves in `application/`, and is addressed by the id of the entity it is a fact about: a read scoped to the wrong entity answers plausibly and wrongly.

## 0002 — Identity foundation

- **Federate; never own a credential.** Sign-in is OIDC to outside providers, and `(issuer, subject)` hashed by `infrastructure/identity/user.py::derive_user_id` is the only identity persisted. No Python auth library: the client is small and ours (`infrastructure/identity/`), and those libraries are abandoned faster than they are adopted.
- **A session cookie is an opaque server-side id, never a JWT** (`infrastructure/identity/session.py::OIDCSessionStore`): a JWT cookie needs a revocation list to log out and carries stale claims. An ID token only crosses a trust boundary and never leaves `presentation/api/middleware/` and `infrastructure/identity/`.
- **`shared/identity.py::IdentityContext` is the sole carrier past the seam**, resolved for every request by `presentation/api/deps.py::resolve_identity` and for the CLI by `application/account.py::local_stores`. No `tenant_id: str` parameter crosses it and no tenant is read from a header or `args.tenant`: `IdentityContext.tenant_id` is the only source of tenant scope, and `infrastructure/store/stores.py::build_stores` takes the identity. The provider is then config, not code.
- **A new identity-bearing ingress first amends [`architecture.md`](architecture.md) § Identity.**

## 0003 — Spend and tenancy

- **Spend rides the per-cycle ledger** as `domain/run_records.py::TokenUsageRecord`, never a parallel pipeline, with identity from the ledger's path. The run's `infrastructure/llm/spend_book.py::SpendBook`, fed by those records, admits every paid send before it goes out, admitted or refused and never queued: a wait inside a cell spends its wall clock.
- **`cost_usd` is provider-reported or nothing** (`infrastructure/llm/openai_compat.py::ReplyBilling`). A call no provider priced is priced at our rate table in `rate_priced_usd`, which every ceiling counts and no surface calls spent.
- **One host-wallet gate, in two units.** `application/jobs/quota.py::admit_launch` bounds an account's whole-ledger use by `config/settings.py::Settings.FREE_TIER_SPEND_CAP_USD` and `FREE_TIER_TOKEN_CAP`, overridable per account. The token arm is the same ceiling in the unit that survives a call with no price. Lifetime, not daily: a resetting cap is unbounded given patience.
- **Admitted whole, or refused; never clamped.** A clamped launch halts mid-campaign, the outcome the ceiling exists to prevent. Admission bounds the whole composed declaration (`quota.py::declare_run_budget`), and what a run is admitted at is what it runs to. The one read-down is a delegate's grant (§ 0005).
- **A run in flight holds its reserve** (`quota.py::_reserve`), subtracted from the next admission's headroom; otherwise two concurrent launches spend one remainder twice.
- **The money axes are the campaign's**, so its live cycles share one ceiling (`quota.py::share_campaign_ceiling`), taken by every launch and pass through `application/jobs/launcher/admission.py::admit_and_share`. `rounds` and `wall_clock_s` are not money and are not shared.
- **Deleting data never un-spends it.** Every destroyer banks the subject's spend (`infrastructure/store/account_spend.py::bank_spend`) after its guard and before its rows go, inside the destroyer, so no caller has to remember.

## 0004 — Operator admin channels

- **A privileged identity or deployment mutation is never an inbound public route.** It goes through an operator-admin channel: an in-zone process that reaches an untrusted message channel outbound only, authenticates the operator in depth (channel token, pinned chat id, optional passphrase), writes through the sanctioned writers and audits to the identity zone, never a tenant ledger. The first is `presentation/admin_bot.py` over `infrastructure/identity/blocklist.py::block_email`; operator-admin reads such as `application/jobs/install_spend.py` follow the same rule.
- **Why:** the blocklist gates authentication itself. An inbound endpoint puts that on the public surface with its credential in a third-party cloud, and the campaign command highway is the wrong I/O kind at the wrong scope.
- **An external orchestrator that must drive an admin action** sits behind an edge service-token broker plus an app token, never a bare endpoint.
- **`/site-records/{table}` is outside the rule**: a token-gated inbound route carrying no identity or deployment state, read by nothing that decides who may act.

## 0005 — Delegated principals and capability scoping

- **Privilege is a capability an identity carries**, not human versus machine: the user's own assistant acts as the user, an external one as an attenuated delegate.
- **Attenuation, never escalation.** A delegate's capabilities are a subset of its delegator's and its spend ceiling at most the delegator's remainder, checked at grant and re-checked at read, one level deep (`infrastructure/identity/grants.py::grant_principal`). Grants live in the identity zone, which a delegate cannot write.
- **One capability seam.** Every verb's capability is a column of `domain/command_kinds.py::CommandKind`, checked by `shared/identity.py::require_capability` at dispatch, and every typed route is pathed by its kind (`presentation/api/routers/commands.py`), so no verb is ungated. Denial is 404, hiding existence. Removing a high rung leaves a bounded one: `campaign.step` advances `step-cycle` without firing an autonomous loop. `campaign.lookahead` is its own rung because it spends the box's shared rate bucket and taints nothing.
- **Babysit is provenance, not an unlock control.** An overlay edit setting a value outside what the node permits (`domain/pipeline_overlay.py::overlay_sets_model_outside_allowed`) needs `campaign.babysit`, warns, and makes every answer that cycle files grade C (`domain/measurement_provenance.py::grade_answer`), which every clean-measurement consumer already excludes. A steer to a permitted value is a clean fork; forking from before the edit escapes the taint.
- **A grant's spend ceiling is a bound, never a declaration** (`application/jobs/quota.py::_delegated_spend_ceiling`).
- **Per-channel grants are not offered until channel identity is unspoofable.**

## 0006 — Embeddable core and extras

- **Core carries what the loop cannot run without; every surface above it is an extra** in `pyproject.toml`, with `all` folding in the operator set. Core is measured by reachability from `presentation/cli/campaign_runner.py` and `application/embedded_run.py`, not argued.
- **Why:** an embedder pays for and must audit every dependency it did not ask for, and a distribution per consumer multiplies CI, releases and drift. One distribution, so an embedded adapter lives here and accepts the release coupling.
- **An extra's import is guarded where a non-installer would hit it**, and the guard names the extra: function-local when the default path imports the module, module-level when only the caller who asked does. `scripts/complexity_ledger.py` counts an unguarded one as debt.
- **A capability that needs a package in core is a design question first**; the answer is usually that it belongs in an extra.
- **LiteLLM is in no core install**: the providers post over httpx, and litellm arrives only behind the `dspy` and `harbor` extras, metered where the `harbor` connector sends (`connectors/harbor.py::litellm_sends_billed_as`). <!-- probe: git grep -cE "import litellm|from litellm" -- promptpotter/infrastructure == 0 -->

## 0007 — One loop, shared members

- **One loop**: generate, score, critique. A paper preset is a named configuration of it, never a second framework, and it states each departure from its paper.
- **A member is shared.** It declares the typed slice of state it reads, so any manifest may name any registered member (`application/optimizers/__init__.py::NODE_MEMBERS`). A feature built inside one optimizer's package alone is the defect.
- **A capability is built once; which node exercises it is a setting.**
- **Variation draws from pools the operator can see and shape** (`domain/pool.py::Pool`), and a value on a child traces to the entry it came from.
- **A choice with two defensible answers is a knob**; a preset picks the value measured to perform. Every entry point controls it, and each control writes the one node overlay.
- **Why:** the research question is the comparison between such features under one instrument, which sealed packages cannot answer.

## 0008 — Content is identity

- **A dataset is its content** (`shared/hashing.py::dataset_hash`, over rows in order); its name is a tenant's label. An answer is filed under `(config_key, sample_key)` and the dataset hash, never a position or a name, so a rename rewrites no row.
- **An individual is `{locus: value}`** over the roster its pipeline declares (`domain/opt_search_point.py::Locus`); a prompt node declaring no fields is refused at parse.
- **Optimizer state is a map of typed parts** (`domain/optimizer_state.py::StatePart`, `StateSlice`, `RoundOutput`); a roster leaving a part open is refused at load.
- **One run declaration** (`domain/campaign.py::RunDeclaration`), one derivation of what it runs (`domain/campaign_config.py::effective_config`), and every door declares through `application/jobs/mint.py::declare`. **One `domain/spend.py::Budget`** bounds USD, tokens, rounds and wall clock; a per-send bound prices one hold and is not a run budget.
- **A round's headline is derived** off its `domain/results.py::Election` and rows, never stored beside them in `RoundOutcome`.
- **Cross-tenant measurement sharing is a policy, refused by default.** The content-addressed caches root at `infrastructure/store/layout.py::archive_home`: the tenant's own tree, unless the host names one install-wide home (`config/settings.py::Settings.SHARED_ARCHIVE`). Off by default because a hit tells one account what another ran and every memory read enumerates every account's populations. Rewriting a home's cells is its owner's, the host alone for a shared one. A replay of another account's cell is priced on `search_incurred` and billed nothing. A store keyed by what one account holds or decided (labels, the δ ruler) stays the tenant's; sharing is safe only because a row names a hash, never a label.

## 0009 — Optimize or compare

- **The purpose is declared on the run and read through one predicate**, never inferred from disk: `domain/campaign.py::RunDeclaration.purpose` is `Optimize | Compare(arm)`, and a run compares exactly when it is declared an arm of a head-to-head.
- **Every mechanism that reads past the run's own declaration states its behaviour under both purposes**, beside its code. One with no stated controlled behaviour is a defect.
- **Fencing withholds; it never biases.** Under control a withheld input is replaced by one paid inside the declaration identically for every arm, or by the mechanism's cold form, never by an estimate from the arms under test. Memory reads go through `infrastructure/store/archive_queries.py::bind_memory_scope`.
- **The individual a race is judged against never shapes the scale it is judged on**, under either purpose: the parent line's rows stay out of the δ fit (`application/bench/difficulty.py::judging_ruler`), and the race's own arms enter it jointly before the election (`DifficultyView.link`), since a shared δ favours no arm.
- **The cache is not memory**: replaying a paid cell by content is the same measurement, admitted under both purposes.
- **Anything is editable at any time by whoever's layer reaches it, and every edit is an event in the run's trace** (operator, 2026-10-29). Reach is layered: the host bounds spend, an owner grants a delegate capabilities ([`operations/access-model.md`](operations/access-model.md)), and the loop reaches least — an optimizer never searches the route, L4 never reaches the instrument. A malformed value is still refused; that is validation, not access. An edit lands hashed into the declaration or applied at a recorded round boundary, so the run stays exactly replayable, and "constant" is derived from the trace (no edit event), never a mode that forbids one. Proposal, not decided: an edit to a controlled arm is admitted and the arm leaves the comparison as that edit's event.
- **Every reading carries its purpose, and a controlled and an uncontrolled reading never share a column.**
- **Why:** a fenced mechanism left to itself fits on the arms under test, and the parent then wins every round.

## 0010 — Every paid pass lean, stoppable, visible

- **No pass scores more than `domain/scoring.py::MAX_PASS_ROWS`** — bench, verify, origin and round alike. A short confidence band is a pass's purpose; rows past it buy no decision. A request or definition asking for more is refused, never silently cut, and `application/scoring/query_loop.py::run_walks` refuses an oversize walk before a cell is sent.
- **Every paid pass stops on the one stop** that pauses a campaign, from every entry point, and lands as a `PassStop` with what it banked (a pause leaves it owed, never faulted: `domain/bench.py::PassStop.state`): a second stop is a stop someone forgets to wire.
- **A stopped or shrunk pass re-pays nothing**: its cells replay by content (§ 0008).
- **A pass in flight is visible on the campaign**, served by the backend, whichever door launched it.
