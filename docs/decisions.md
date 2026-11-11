# Decisions

Each entry is a boundary and why it sits there. Paths are under `promptpotter/` unless they start with `scripts/`.

## 0001 — M12 control plane

- **The wire is declared, never handwritten.** Payload and record models generate the OpenAPI (inbound) and AsyncAPI (outbound) specs; a command or event kind declares its model before its handler lands.
- **Commands are records on one account log, never a second pipeline.** `application/commands/dispatcher.py::CommandDispatcher` files every `CommandRecord` under the subject it addressed, whichever door sent it; a cycle ledger keeps what happened, never what was asked, so no ledger move or delete reaches a command. `applied` is acked only when the effect happened; a client believes only acks.
- **Outbound frames have no writer.** SSE tails the on-disk ledger; identity scope rides the ledger's path, never a per-record `tenant_id`.

## 0002 — Identity foundation

- **Federate; never own a credential.** Sign-in is OIDC to outside providers; the hashed `(issuer, subject)` is the only identity persisted. No Python auth library: the client is small and ours.
- **A session cookie is an opaque server-side id, never a JWT.** An ID token never leaves `presentation/api/middleware/` and `infrastructure/identity/`.
- **`shared/identity.py::IdentityContext` is the sole carrier past the seam**; no tenant is read from a header or an argument. A new identity-bearing ingress first amends [`architecture.md`](architecture.md) § Identity.

## 0003 — Spend and tenancy

- **Spend rides the per-cycle ledger, and the account is charged at the same send**: a cycle's ledger is run data a wipe takes, an account's bill is not. The run's spend book admits every paid send before it goes out, admitted or refused, never queued.
- **`cost_usd` is provider-reported or nothing.** A call no provider priced is priced at our rate table, which every ceiling counts and no surface calls spent.
- **One host-wallet gate: the host coupon.** It bounds what the box's keys pay for an account over its life; past it the account sends on its own key. What is left is a fold of charges by payer, never a counter. **Why lifetime, not daily:** a resetting cap is unbounded given patience.
- **One rule names who pays a send:** `application/jobs/coupon.py::paying_key` — the account's own key, else the box's, else none; `infrastructure/llm/registry.py::get_llm_client`'s callers are `application/jobs/coupon.py::keyed_client` and `connectors/harbor.py::_AgentModel` (the box's key, `CONNECTOR.cell_payer`). A connector declares who pays its cells and whether a rate prices them (`cell_payer`, `cells_priced`), and `application/jobs/quota.py::launch_refusal` refuses a launch `no_api_key`, then `budget_unenforceable` (a dollar ceiling over unpriced cells), then `host_allowance_exhausted`. <!-- probe: git grep -cE "get_llm_client\(" -- promptpotter/*.py <= 3 -->
- **Admitted whole, or refused; never clamped.** A run runs to what it was admitted at, and a run in flight holds its reserve against the next admission.
- **The money axes are the campaign's**, so its live cycles share one ceiling; `rounds` and `wall_clock_s` are not money and are not shared.
- **Deleting data never un-spends it.** Every cycle destroyer banks its spend before its rows go.

## 0004 — Operator admin channels

- **A privileged identity or deployment mutation is never an inbound public route.** It goes through an operator-admin channel: an in-zone process reaching an untrusted message channel outbound only, authenticating the operator in depth, auditing to the identity zone. **Why:** the blocklist gates authentication itself; an inbound endpoint puts that on the public surface with its credential in a third-party cloud.
- **An external orchestrator that must drive an admin action** sits behind an edge service-token broker plus an app token, never a bare endpoint.

## 0005 — Delegated principals and capability scoping

- **Privilege is a capability an identity carries**, not human versus machine: the user's own assistant acts as the user, an external one as an attenuated delegate.
- **Attenuation, never escalation.** A delegate's capabilities are a subset of its delegator's, never the account's keys, checked at grant and at read, one level deep. Grants live in the identity zone, which a delegate cannot write.
- **A delegate carries no spend bound of its own**: its delegator's coupon is its one bound. **Why:** a grant's spend ceiling had no writer, and where set it clamped each launch, never the delegate.
- **Per-channel grants are not offered until channel identity is unspoofable.**
- **One capability seam**, checked at dispatch; denial is 404. Removing a high rung leaves a bounded one: `campaign.step` keeps the bounded controls and launches nothing; `campaign.lookahead` is its own rung because it spends the box's shared rate bucket and taints nothing.
- **Babysit is provenance, not an unlock control.** An overlay edit outside what the campaign permits needs `campaign.babysit`, warns, stamps the fork, and grades every answer that cycle files C, which every clean-measurement consumer excludes.

## 0006 — Embeddable core and extras

- **Core carries what the loop cannot run without; every surface above it is an extra.** Core is measured by reachability from the CLI runner and `application/embedded_run.py`. **Why:** an embedder pays for and must audit every dependency it did not ask for; one distribution, so an embedded adapter lives here and accepts the release coupling.
- **An extra's import is guarded where a non-installer would hit it**, and the guard names the extra.
- **LiteLLM is in no core install**: the providers post over httpx; litellm arrives only behind extras, where it sends nothing of ours. <!-- probe: git grep -cE "import litellm|from litellm" -- promptpotter/*.py == 0 -->

## 0007 — One loop, shared members

- **One loop**: generate, score, critique. A paper preset is a named configuration of it, never a second framework, and states each departure from its paper.
- **A member is shared.** It declares the typed slice of state it reads, so any manifest may name any registered member. A feature built inside one optimizer's package alone is the defect.
- **Variation draws from pools the operator can see and shape**, and a value on a child traces to the entry it came from.
- **A choice with two defensible answers is a knob**; a preset picks the value measured to perform. **Why:** the research question is the comparison between such features under one instrument.

## 0008 — Content is identity

- **A dataset is its content**; its name is a tenant's label. An answer is filed under `(config_key, sample_key)` and the dataset hash, never a position or a name.
- **An individual is `{locus: value}`** over its pipeline's roster (`domain/opt_search_point.py::Locus`); a prompt node declaring no fields is refused at parse. **Optimizer state is a map of typed parts**, and a roster leaving a part open is refused at load.
- **Cross-tenant measurement sharing is a policy, refused by default.** **Why:** a hit tells one account what another ran. A store keyed by what one account holds or decided (labels, the δ ruler) stays the tenant's.

## 0009 — Optimize or compare

- **The purpose is declared on the run and read through one predicate**, never inferred from disk: `domain/campaign.py::RunDeclaration.purpose` is `Optimize | Compare(arm)`. Every mechanism that reads past the run's own declaration states its behaviour under both, beside its code.
- **Fencing withholds; it never biases.** Under control a withheld input is replaced by one paid inside the declaration identically for every arm, or by its cold form, never by an estimate from the arms under test. **Why:** a fenced mechanism left to itself fits on the arms under test, and the parent then wins every round.
- **The individual a race is judged against never shapes the scale it is judged on**: the parent line's rows stay out of the δ fit, and the race's arms enter it jointly before the election.
- **The cache is not memory**: replaying a paid cell by content is the same measurement, admitted under both purposes.
- **Anything is editable at any time by whoever's layer reaches it, and every edit is an event in the run's trace.** The host bounds spend, an owner grants a delegate capabilities, and the loop reaches least — an optimizer never searches the route, L4 never reaches the instrument. An edit lands hashed into the declaration or at a recorded round boundary, so the run stays replayable.
- **An act on a controlled arm is admitted, and the arm leaves the comparison as that act's event**, through one door that alone reads the purpose. **The arm is the campaign, never a line**, since a fork spends the campaign's one ceiling. **Why:** a refusal on some acts and an exit on others is two doors for one input. <!-- probe: git grep -cE "isinstance\([a-z.]*purpose, Compare\)" -- promptpotter/application/commands/*.py == 0 -->
- **Every reading carries its purpose, and a controlled and an uncontrolled reading never share a column.**

## 0010 — Every paid pass lean, stoppable, visible

- **No pass scores more than `domain/scoring.py::MAX_PASS_ROWS`**; rows past a short confidence band buy no decision. A request for more is refused before a cell is sent, never silently cut.
- **Every paid pass stops on the one stop** that pauses a campaign, or on its command's `cancel`, from every entry point; a pause leaves it owed, never faulted, and its cells replay by content (§ 0008). A pass in flight is visible on the campaign, whichever door launched it.

## 0011 — What outlives a release

- **Run data is disposable; a survivor is not.** A survivor says who an account is and what it agreed to, what it authored, what it was charged and launched today, or who the host is; a cache is content-keyed and paid for; the rest is run data a reshape wipes. **One table names every file**: `infrastructure/store/data_root.py::KINDS`. A file no row names is a bug in the table. `reset` is that table applied.
- **A survivor is migrated, never wiped; run data is never migrated.** A reshape of a survivor ships a one-shot migration as a release step, never a reader of two shapes, deleted in the release after it lands.
- **A landing proves the whole data root as its migrations would leave it, then writes it, and nothing rolls back after**, and every process lands the root at its entry, once per record schema. A survivor that does not load refuses the landing, and so does a file no row names; a cache or run data that does not load is dropped unasked.

## 0012 — The host is whoever the user talks to

- **PromptPotter serves one surface; a host drives it**, in MCP's shape, in-process to the webapp's assistant and over MCP to any other host. **Why:** a check-in built as a gate in front of the optimizer serves the webapp alone, and two surfaces drift.
- **The origin gate stays the contract**, decided server-side; a host never talks past it.
- **The account's own assistant acts as the user** (§ 0005), is charged like any send (§ 0003), and fires a write only on the user's click. Open work: [`specs/roadmap.md`](specs/roadmap.md) § Host assistant.
