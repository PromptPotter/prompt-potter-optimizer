# Roadmap

Forward work in execution order, each item with the check that closes it. Why a boundary sits where
it does is [`../decisions.md`](../decisions.md)'s; debt is [`code-debt-cleanup.md`](code-debt-cleanup.md)'s.
Any new endpoint is multi-tenant by default.

## Sequence

### The cleanup arcs (plan review, 2026-11-07)

The code debt, worked as chains of small links in the order below, as far as it gets: a sequence, not
a schedule. Sections named in a row are [`code-debt-cleanup.md`](code-debt-cleanup.md)'s; "host step
N" is [`host-assistant.md`](host-assistant.md) § Build order. How a link is sized, briefed, landed,
reviewed and stopped is [`../developer/refactor-arcs.md`](../developer/refactor-arcs.md)'s. "Was"
names the earlier arc a ledger row may still cite.

- **The backbone first.** K0, M3, M1, DOORS and M2 landed in batches 10 to 12, M4's re-key in
  batches 13 and 14. CONN finishes the backbone.
- **Where.** A chain that needs no box, no TermNorm checkout, no paid call and no phone shot runs in
  the cloud, one session per chain. Docs, the gate, phone shots, TermNorm and fix passes run locally.
- **Model.** Sonnet for a mechanical sweep, Opus for judgement. Fable plans; it executes no arc.
- **Deploying main waits for the operator's word.** Main keeps landing. The end: the code debt is
  rewritten, then the operator is asked about a release.
- **An item ships when its code-debt entry is deleted.** A shape noticed in passing is fixed at its root
  in the same arc, unless it is a fork.

| Arc | Was | Closes | Decision first | Waits on |
|---|---|---|---|---|
| **M4** typed pipeline carrier | U1a, M4c | engine A's rest: the carrier's nodes typed by `NodeCall` (44 engine-key reads); the setup screen's knob layer and Reset | the re-key landed (batch 14) | - |
| **CONN** connector identity | M4b, with rows of M6, M7, M12, M18 | engine A2; one resolver that refuses an unregistered connector typed at every lookup; `init_services` takes the campaign's connector and refuses a mismatch; `backend_type` becomes `connector`, the definition key included; `PipelineSchema`'s name and version; `index_terms` once; the defaults' two owners; `DEFAULT_CONNECTOR` and the endpoint default; a connector's spend, payer and wire kind declared | the landing re-writes tenant definitions (yes) | M4 |
| **LAND** the landing as a precondition | M17's landing rows | the landing at every process entry, keyed on the record schema the code writes; it drops unloadable run data by itself and refuses unknown files alone; `block_packs/_sources` and its builder move into the checkout; one remedy for an unreadable file; a shadowed manifest proven through its reader; `LedgerUnreadableError` folded into `FileUnreadableError` | yes (operator, 2026-11-07): every later wipe is a landing step | lands before the release that carries the first wipe (operator, 2026-11-08) |
| **READS** | M17's read rows | `banks_sharing`, the `Fate.RUN` dict rows, a whole-file read refused four ways, `CACHE_PATH` | read through `io.py`'s readers | — |
| **M5** running set | RS | RS, census F's `exclude_nodes`, the one-node rule | accept the L4 cache miss (yes) | M4 |
| **M6** one start | U2 | `RunStart` and `HeldRun`, `LaunchError`, config item 6 (slot lifetime), the one launch answer, the double selection, `draft_declared`, a task opened only for an admitted act, a queued launch promoted by whoever frees the slot | plan and price before admission for every start (yes) | — |
| **M7** run init and round 0 | U2 | batch 3's init and round-0 rows, config item 5, `--diag` as a mode, the test builders, the origin-gate rows, `_peer_cycle`, a resume that takes 72 s to apply | halt a degraded origin (yes) | M6 |
| **ADDR** an address off its record | — | the fork-summary kind, the webapp's id parses (`rootCycleId`, the short labels), parent counting | the mint record serves the kind and the short label | M7 |
| **M8** potter call door, served roster | U1b, EP | `InjectionBundle`, `Calls.ask`, the seed rule, `PROMPT_FIELD`, swept F, the `[#N]` slots, the panel's money text, a refused L4 arm's identity | a failed critique is a typed fault (yes) | M4 |
| **M9** ledger read stack | U3 | the projection checkouts re-cut on M2's account log (M9.5a, M9.5b, M9.2, M9.6), `_unlanded`, the cold-fold snapshot (held: re-measured on the box first), the `dashboard.json` checkout, the swallowed round checkout; a derived file's fault never unwinds or stops a landed line | keep `dashboard.json` / `index.json` as projections (yes, §0) | - |
| **M10** narration and tracing | U3, ET | one narration table, the typed phase act, ET, `ActivityItem` as `Said`, a pause's cause served | — | M9 |
| **M11** typed run records | U3 | engine B and C, `StatusMark`, `rewound`, the act record, the cell beat, `cached` / `key_source`, the export's spend, the on-disk renames (`ArmSlot`, `CycleSeed`, `LinePayload`) | — | M9 |
| **M12** spend | S | the book counted once, one charge value, the lost mirror, the reserved hold, stale `run_limits`, a grant verb, the host-coupon rows | a re-grant counts from `granted_at` (yes); migrates `charges.jsonl` | M9 |
| **M13** one scoring pass | U4 | § One record shape, the artifact writers outside the roster, the skipped verify, noise-floor and seed-screen on the ledger, the catch-up cells, the verify that starts 38 s late and is never acked `accepted` | the verb is "held-out" (yes) | M9 |
| **M14b** faults as values | Q3 | `logging` leaves `application/` and `domain/`: a mechanical sweep with no other arc open | — | M9, M10 |
| **FEED** change feed | Q2, M15 | Q2, census E | a subject is the `Subject` path (yes) | M9 |
| **M16** webapp (a, b) | swept C, U5 | lineage reads the served tree; one arm row; the selection in the hash | a swiped-in column stays blank until its reads land (yes) | FEED |
| **M18** TermNorm's edge | G | the in-process `llm_only` connector, the wire-skew probe, TermNorm's wording and `prompt_info`, a named endpoint per connector, § Bypasses; local, in the TermNorm checkout | edit TermNorm where the arc needs it (operator, 2026-11-06) | CONN |
| **M19** the election | D | the election root, the collapse gate, the lift bars, `CatchUpPool`, CAPO's survivor ranking | offline first, then priced | M13 unconfirmed |
| **M20** MCP over HTTP | — | the MCP door served over Streamable HTTP behind the API's sign-in, a second transport of the one adapter | auth per [`access-model.md`](../operations/access-model.md) (yes) | — |
| **M21** Turns | B | § Turns — the unsupervised limit, on M6's admission; `useRemoteModel`'s bag | — | M6 |
| **M22** host steps 4 and 5 | A4 | the host loop, the chat, the check-in's ingest | host-assistant § Open (operator) | — |
| **M23** host step 6 | A5 | host step 6, with the "Run the resolver now" row | — | M22, M6 |

**Edges** (→ means before): M4 → CONN, M5, M8, M9. M6 → M7 → ADDR. M9 → M10, M11, M12, M13, FEED.
M13 → M19. FEED → M16. M10 → M14b. CONN → M18. LAND lands before the release that carries the
first wipe (M2's, on main since batch 12; M9's round-audit cache, CONN, M10 and M11, M12, M13).

**Critical path:** M4's re-key → M9 → M13 → M19, then the next paid run. M9 → FEED → M16 runs
beside it.

**Next:** batch 15: M9's re-cut (`w7m9b`, census 7c) on M4's re-keyed tip; then CONN, and LAND before
the release.

**Re-keys.** M4, CONN, M8, M5 (the L4 cache) and M19 (decisions) land in any order before the next
paid run. M4's and M8's re-keys share one cold cache only if no paid run lands
between their merges.

**Last, at sync points with no open chain in their trees:**

| Arc | What | Waits on |
|---|---|---|
| **LA** | § The file layout, Wave A | no open chain in `presentation/` or `infrastructure/` |
| **LB** | Wave B | M9 |
| **LC** | Wave C, as one re-key batch | M8, M19 |
| **LW** | the webapp modules that break `lib/CLAUDE.md` § Where a module lives; the barrel cycle | M16, FEED |
| **TS** | the test suite consolidated after the deletions; tests go through each module's interface | LB |
| **R** | § Open — renames the operator picked | every other arc (it runs last) |

The paid run comes after M19 and the re-keys: the bench's CAPO head-to-head on `hotpotqa-closedbook`
(§ The bench), priced first. Then the board (§ Optimizer tuning — the standing board).

**Forks left to the operator.** Until he decides, an arc leaves each one as it stands:
- what "calls held equal" holds equal;
- a bench's total (the box's definition is read first);
- the sub-cent money rule;
- whether an upload's bytes are kept before the parse;
- what `hit_spread` means;
- how API keys are stored at rest;
- one env file on the box;
- one email holding two accounts;
- an archived answer's per-node outputs;
- the crowning bar (paper stage 3);
- everything under § Blocked.

### After the cleanup

1. **Recombination at $0** (C9). Paid: the mode head-to-head (§ Variation and pools, item 1).
2. **The optimizer climbs** (§ Optimizer tuning — the standing board). Paid: the board's path, steps
   (1)–(4).
3. **The preprint's evidence.**
   - Code: the N-arm mint verb, and `bbeh` re-ingested with a wider bench partition.
   - Code: anytime-valid confidence sequences become the default stopping rule. Today's PoBB stays a
     selectable rule, and a replay over banked rounds reports where the two disagree
     ([`../research/external-constraints.md`](../research/external-constraints.md) § Ranked, item 2).
   - Paid, priced in dollars and wall clock first: the comparability run, then the budget dimension
     (§ Order, items 5 and 6), then the ablation rows.
4. **The hosted beta (Lane A):** A1, the first-run exhibit, built on a campaign that climbed; then A3,
   the anonymous preview.
5. **Capability (Lane C), by dependency:**
   - C2 composite fitness, then C4 cross-user panel;
   - C2b judged scoring;
   - C3 L4 closure;
   - SIFT S1;
   - the PUCT selector (§ Selector members — parent choice as one acquisition);
   - C6, C7, C8, C10.
6. **Embedding (§ Embedding (M15)):** headlined by the Langflow setup. R1 comes first, because C5 and R2
   wait on it; the rest are independent of each other.

Lanes by dependency. Front priority: Lane A and the bench lane, concurrently; C9 ahead of
the rest of Lane C.

### Lane A — the hosted beta, usable by a coupon account end to end

- **A1** First-run exhibit — a finished campaign a new account reads before supplying anything (§ First-run exhibit).
- **A3** Anonymous preview tier — the public site's chat before sign-in (§ Anonymous preview tier).
- **A4** Arrangeable workspace — panels the operator resizes, collapses, pops out and docks back (§ Arrangeable workspace).

### Lane C — differentiator and capability

- **C1** Host assistant: the account's one conversation, the check-in its first job, one surface for every host — [`host-assistant.md`](host-assistant.md).
- **C2** Composite fitness P2–P4 (§ Members the presets need).
- **C2b** Judged + turn-structured scoring: the per-step ruler (§ Judged and turn-structured scoring).
- **C3** L4 closure — [`l4-outer-loop.md`](l4-outer-loop.md) § Open.
- **C4** Cross-user measurement panel, after P3.
- **C5** MCP over streamable HTTP (§ Agent-tool parity), on R1's credential; the surface is C1's · the editable `dataset.yaml` `pipeline` write side · the connector as a unit (§ The connector as a unit).
- **C6** Public-service hardening: metrics, billing; the image is R9.
- **C7** Agent harnesses (§ Evolving agent harnesses); non-prompt targets and multimodal are R5–R7 (§ Embedding (M15)).
- **C8** Mask abstraction — the sample-set half (§ Lineage mask).
- **C9** Variation and pools (§ Variation and pools).
- **C10** Operator-authored prompt-field order (§ Prompt-field order).

**The preprint** needs: ablation rows L1 / L1+L2 / full (`paper/plans/ablation.yaml`) · scan · cross-run archive · critique ·
zero-signal-filter; competitor and L4 numbers after C3. Admission bar and roster: [`../research/benchmarks.md`](../research/benchmarks.md).

## The bench

Every optimizer, peers included, is a manifest the bench runs, inheriting measurement,
archive, spend ledger, persistence, the five entry points and the control plane; the headline is the
held-out bench score. It closes when ONE peer (CAPO first) runs end to end in a head-to-head whose
only difference is the optimizer. **The preprint:** the stopping rule and the comparability
guard are executable here and every peer ran under them ([`../../paper/`](../../paper/README.md)).

The contract is [`../architecture.md`](../architecture.md) § Bench and optimizer; node types are
[`../developer/node-standard.md`](../developer/node-standard.md) § Optimizer node types. Two rules a
member must keep: **it is handed its node's `NodeContext`, never `Cycle`** (a store handle or live
client in its input moves the core boundary and the trusted-code story); **its own sends are
admitted before they leave** (`infrastructure/llm/spend_book.py::admitted`).

- **The compare pane does not render the head-to-head headline** (`GET /evidence::head_to_head` serves it).

### Order

5. **The comparability run** (PAID): BBEH, one target model, one grader, one bench set, arms
   `optimizer ∈ {potter, capo, levi, gepa}`, calls held equal, 3 seeds; paired per-cell differences,
   clustered SEs, Holm/BH across the matrix. The controlled reading (one declared head-to-head) and
   the uncontrolled one (each peer run its own way, the notebooks) are never substituted. Owed: `bbeh`
   re-ingested with a bench partition wider than 100 rows. Declared as
   `paper/plans/comparability.yaml`; price it with `price-head-to-head` before asking.
6. **The budget dimension** (PAID, after 5): the same arms at two or three budget points — the
   budget-matched Pareto comparison reviewers ask for first (`paper/plans/budget-dimension.yaml`).

### Members the presets need

- **Composite fitness (C2).** P2 per-candidate rollup + scatter panel (after P3) · P3
  `compile_post_aggregate_fitness(formula)` + `campaign.scoring_post_aggregate` · P4 multi-objective
  racing: a `pareto` selector with an eliminator reading the front.
- **SIFT's members** (§ SIFT reproduction): the self-proposer (the parent agent on its own source
  and logs, one patch out); the archive-wide parent selector `P(i) ∝ exp(−α·r_BT − β·r_acc − η·log(1+v))`,
  whose inherited-accuracy rule may steer a choice, never become a measurement; a candidate-level
  pairwise judge + regularized Bradley-Terry; the `fixed_gate` eliminator.

### Selector members — parent choice as one acquisition

Potter's selector (`application/scoring/selection.py::elect_round_winner`, greedy) is UCB1 rebase
(`application/mask/backprop.py::select_branch_round`) with the exploration term at zero. Direction:
**one PUCT acquisition `Q + c·P·√ΣN/(1+N)`**, P a predicted lift in θ units on `L1Variant`
(never a normalized preference). `fork_proposal`'s contract does not change. No learned value model
until a surrogate's held-out error is measured below the lifts the loop chases; Q stays measured θ.

1. **Bank the predicted lift as an inert field** and compare it with realized θ lift — built first,
   shipped with its display (one mark per arm, read by state; an arm the eliminator cut shows its
   prediction as resolved without measurement).
2. **Serve what the acquisition cannot otherwise report:** visit concentration, value vs exploration
   term, prior entropy across arms, calibration trend, and ruler provenance on a backpropagated Q
   (`NodeStats.value_sum` mixes scales under `prior_pinned`).
3. **The rebase forfeit as its cost term** — a pure fold beside `divergence.py` emitting a per-cut
   `ForfeitReading`, served first, then `Q − λ·forfeit`; lands as ONE function with the acquisition.

Open: what stands in for θ on unmeasured children; forest vs round normalization; whether a cut arm
is a legal parent; names (`selection`, `prior`, `value_sum` → `theta_sum`).

## SIFT reproduction

SIFT-exact is a `sift` preset (a frozen sampler, `fixed_gate`, no controller, one variant per round),
and the paper is the ablation ladder from it: one potter member swapped in per arm at one budget.
Models: the dataset's own; S3's agent `qwen/qwen3.7-flash`; the candidate judge `openai/gpt-oss-120b`.
Code-artifact candidates (a content-hashed file set loaded per episode) are the design-bearing gap.

- **S1** — the judge, parent selector and `fixed_gate` on a single-call dataset (`justlogic/d234`),
  opening with the judge's ranking against realized θ (bar ρ≈0.68). *Closes when* SIFT-on-prompts vs
  SIFT-on-prompts + PoBB is served by `evidence`.
- **S2** — the self-proposer. *Closes when* an `optimizer: sift` round proposes through it.
- **S3** — code artifacts + Polyglot, only if S1 makes it worth it; the sandbox is decided then.

## Optimizer tuning — the standing board

The goal is the price of a unit of optimizer progress, per dollar and per minute. How to work it is
the `potter-self` skill's; numbers are [`../research/benchmarks.md`](../research/benchmarks.md)'s. The
operator's bar: at least +10 points on a held-out bench in about five rounds, then the same lift in
less wall clock, on an official dataset with a natural origin below 0.4. A one-key task is a smoke test.

**The path:** (1) replicate the nearest climbing setting and read what the proposer saw of the
misses; (2) screen library blocks without the optimizer — authored arms, origin plus ONE block;
(3) the optimizer-model screen, per method and per step — potter's L1 moved to MiniMax (jl-optmodel-1);
(4) combine the winners.

**Hypothesis under test:** each method, and each step within one, has its own most efficient model
([`../../paper/notes/model-per-method.md`](../../paper/notes/model-per-method.md)).

**Defects** are [`code-debt-cleanup.md`](code-debt-cleanup.md) § Open — optimizer tuning.


## Variation and pools

The contract is [`variation-and-pools.md`](variation-and-pools.md); why is
[`../decisions.md`](../decisions.md) § 0007. Part of the bench; each preprint ablation row quotes
`rounds_to_separable`. It must not become a fifth sealed optimizer, a second overlay beside
`optimization.nodes`, a pool written by a file edit, or a credit model steering before a reading
shows it informative.

1. **The mode head-to-head** (PAID), the host the only difference, matched on USD and cells.
2. **L4 reach** — the mix as an outer-arm lever, on the inner cell's overlay and identity.
3. **A pool fed while a campaign runs** — on a controlled arm it leaves the comparison, applied at the next round
   boundary, replayed on resume.
4. **Paper compositions** — EvoPrompt GA/DE, PhaseEvo, GEPA's merge, each stating its departures.

Stages 2, 3 and 4 share no seam.

## Probe rounds

A probe round spends its budget on ONE narrow question: more candidates on a narrower question.
`L2ContextOutput` carries no `action` field for it. Build it as: the target picked from measurement
(a peaked axis, a recurring failure cluster), `n_variants` widened for that round, samples chosen by
relevance to the hypothesis — never by a set that is empty on a healthy run. Re-running warned
queries is degradation triage, not a search action.

## Turns — the unsupervised limit

**A turn is what the system may spend on its own between two human Plays** — an action budget, not
a campaign budget (operator, 2026-11-02). Today only two bounds exist: the campaign ceiling
(`domain/spend.py::Budget`, shared by its live cycles) and the account's granted allowance
(`application/jobs/coupon.py`). Nothing bounds one unattended stretch, so a run either stops at a
ceiling the operator must hand-raise, or spends to the allowance.

- **Play buys one turn of the same amount**, drawn from the account's allowance and admitted whole or
  refused (§ 0003, never clamped). A spent turn PAUSES the run, resumable; it never stops it.
- **The campaign ceiling is off by default**: the turn is the unattended bound, the allowance the hard
  one. A ceiling stays an opt-in cap.
- **The turn count is a fact on disk** (each Play a ledger record), served with what the next turn
  costs and what the allowance has left, so the Play button reads "turn 3 — $0.03 of your $4.70".
- Every entry point buys a turn the same way (CLI `resume`, webapp Play, the host assistant's run
  tool); the host's own paid calls spend the current turn.

- **A Play the next turn cannot use is refused before a job launches**: today a run at its rounds cap
  launches on Play and ends at once (`SUCCESS · Rounds budget reached`, box, 2026-11-02, three times
  in a minute), which reads as "it starts, then doesn't".

**Closes when** a run paused at its turn's end resumes on one Play, the ledger counts the turns, and a
Play the allowance cannot cover is refused naming the allowance.

## The right winner — redundant mechanisms

**Potter elects wrong winners today, and the operator sees it at a glance** (operator, 2026-11-02).
The evidence is box `gsma_telelogs_5g_root_cause_full__bfdc26` / `cycle_11a2a7f0e73f`, rounds 1–7, with per-round resubset on. Three of four winners were LOCKED IN on part of their panel:
- C1.2 at 6/15.
- C2.3 at 7/13. As the parent it later read 8/20, 10/20 and 8/20.
- C5.6 at 9/12. As the parent it later read 13/20, then 7/20.

Round 4 elected nobody, although C4.4 and C4.3 each read 12/20 against the parent's 10/20 on the full panel (lift/se 0.79, under `theta_election.admission` 0.85). The paper pilot names the cause: PoBB's posterior bar, read after every cell, locks in falsely about four times as often as it states ([`../../paper/notes/stopping-rule-false-stops.md`](../../paper/notes/stopping-rule-false-stops.md)).

**Lock-in stays a goal, past real significance.** Several mechanisms, each catching what another misses:

1. **Closed: L2 sees the elections and can re-elect** — `fork_proposal.stand_on` behind `reelect_capability` ([`../../promptpotter/application/optimizers/potter/CLAUDE.md`](../../promptpotter/application/optimizers/potter/CLAUDE.md) § The layer-control channel — `fork_proposal` + `terminate_proposal`). Check: `tests/test_scoring.py::test_the_ladder_climbs_and_the_run_stops_only_on_evidence_it_resolved`, a fork standing on an unelected arm re-elects it.
2. **Then, at the root: the election picks the right winner with lock-in on.**
   - **Lock in only past an anytime-valid bound.** The pilot's rule is a mixture likelihood ratio on θ at level α/K per arm. PoBB's per-cell posterior bar stops being a lock rule. This pulls Sequence item 4's "anytime-valid confidence sequences as the default stopping rule" forward. Until it lands, potter's `pobb` `lock_in` switch ships off. A review of a first build measured what the bound must do:
     - (a) read the verdict direction as the election does;
     - (b) split α over the round's actual K, not the most arms a round could race;
     - (c) read the parent pair on cells the parent's grades did not choose: the hard-first walk otherwise breaks Ville's guarantee (false locks 31.5% against 2.0% at α = 0.5);
     - (d) expect little power: about 1% for a +1-logit arm within 20 cells at 15%.
   - **The standing pick's evidence pools across rounds.** Its reads as parent on each round's fresh subset count toward its θ, so a lucky lock-in is dethroned by the same election (C5.6 by round 7).
   - **A near-miss carries forward.** A round's best challenger that cannot yet be separated from the parent re-enters the next round's panel and accumulates paired cells, instead of being dropped (C4.4 and C4.3 in round 4).

Each change moves what the loop decides: diff it with [`../developer/offline-run.md`](../developer/offline-run.md). A changed ledger shape wipes run data (from 2026-11-03).

**Closes when:**
- an offline replay of that cycle elects no pick that its later reads as parent contradict;
- the pilot's equal-arms simulation shows false locks at the stated level on the landed rule.

## Origin-resolution check-in

Invariants: it reuses the `checkin/2` node; `reasoning_floor`/`ceiling` stay off the operator
surface; model and provider are optimizer-locked.
- **Run mode** — the same node raises `pause-cycle` / `change-run-limits` / `fork-cycle` over
  `application/datasets/origin_resolve.py`'s `RaisedCommand`. Lands with L4.
- **The whole setup proposed from the operator's text** — every node's model and effort, rounds and
  cap — priced against the served menu and `application/jobs/quota.py::launch_bound`; the host's
  bound outranks the operator's words; an off-menu proposal is dropped at `_coerce`.

## Embedding (M15)

PromptPotter is the optimizer a program calls instead of holding one; a text LLM is one model kind
among many; the hosted tier and the repository hold when many people arrive at once. **The bench
stays small — it proposes, admits, measures, decides and archives; a program, a model kind or a
framework arrives as a declaration, a program answering its own cells, or an entry-point plugin, and
no R-item adds core code naming one.** R1 comes first (C5 and R2 wait on it), then R2; the rest are
independent of each other, each closing on its own check. Findings:
[`../research/external-constraints.md`](../research/external-constraints.md) § M15.

**The headline setup — Langflow.** What PromptPotter recommends to someone who knows AI assistants,
has never opened a shell, and wants AI infrastructure of their own at home rather than one campaign.
Langflow builds the pipeline visually; PromptPotter tunes the flow through Langflow's per-run tweaks,
held by `LANGFLOW_TWEAKS_POLICY=declared` to the fields the flow marks API, the declared-knob
boundary enforced on Langflow's side — a flow that marks none is permissive, so the starter's flow
marks its knobs and the pulled program refuses a flow with none. Shipped as one starter in
`examples/`: a flow, the Langflow pulled program (R2) and an agent skill that walks the user's
assistant through each step. NVIDIA OpenShell is the starter's optional sandbox — policy-bound, the
editor's port forwarded (`openshell sandbox create --forward`) — until its maker commits to it with
a 1.0 or a support statement; Windows, where it is experimental, runs Langflow Desktop alone.
*Closes:* someone who has never used a shell reaches a finished campaign on their own flow, their
assistant doing the typing.

**Reachable**
- **R1 The machine principal.** A delegate grant ([`../decisions.md`](../decisions.md) § 0005;
  `infrastructure/identity/grants.py::grant_principal`) as `Authorization: Bearer` — an opaque
  server-side id like the session cookie, resolved by `presentation/api/deps.py::resolve_identity`
  under the same rate buckets. Both mints stay federated, so § 0002 holds: a workload's signed JWT
  under OAuth client credentials (a CI job's OIDC token, MCP's machine-client extension), and an
  interactive login — MCP's OAuth (C5), or a device code (RFC 8628) for a program with no browser. A
  pulled program holds one new rung, `campaign.answer`. Never "API key": `set-api-key` is a
  provider's key. It opens with a [`../decisions.md`](../decisions.md) entry naming the
  authorization server — MCP's OAuth 2.1 makes this server a resource server, a device-code mint
  makes it the token issuer — and the [`../architecture.md`](../architecture.md) § Identity amendment
  § 0002 asks of a new identity-bearing ingress. **No route takes an API key** — owned by
  [`../developer/stable-api.md`](../developer/stable-api.md) § 8; R1 rewrites that line when it
  ships. *Closes:* a CI job reads `evidence` and mints a head-to-head with no cookie.
- **R2 Pulled cells.** `domain/connector.py::ConnectorExecution` gains `pulled`: the loop offers each
  admitted cell on the cycle's ledger; the program leases one and answers through
  the `CommandDispatcher` on R1's credential, the answer a `/matches` body read by the reader every
  connector uses. The box dials nothing a tenant names and runs no tenant code, so the 3c trigger
  ([`../operations/access-model.md`](../operations/access-model.md) § loop ↔ everything) stays
  unpulled. An unleased offer waits like an unreachable backend; a leased cell past
  `cell_envelope_s` is `HALTED`, never re-offered; answer text is untrusted (§ Connectors, Phase 2).
  Open: whether `in_process` is `pulled` over a local transport. *Closes:* a program outside this
  tree runs a hosted campaign, every cell answered on its own machine, nothing under `promptpotter/`
  naming it.
- **R3 One versioned wire.** The routes a delegate reaches and the cell wire join
  [`stable-api.md`](../developer/stable-api.md), each carrying a version a peer refuses on; breaking
  stays free. A Python client with no dependency, a TypeScript one generated from
  `openapi.generated.json`. *Closes:* one pulled program per language against one spec.
- **R4 Host-declared providers.** `infrastructure/llm/registry.py::_OPENAI_COMPAT_SPECS` becomes host
  configuration, each entry with its rate, so any OpenAI-compatible host is a line; tenants still
  name no provider. *Closes:* one campaign on build.nvidia.com and one on a self-hosted vLLM, with no
  edit under `promptpotter/`.

**Any model** — targets in the evidence order of [`../research/external-constraints.md`](../research/external-constraints.md)
§ M15, retrieval parameters first and video last.
- **R5 Typed parameter domains.** `PipelineNode.param_allowed_values` holds strings alone; a domain
  becomes an enum or a numeric interval (`low`, `high`, `step`, `log`, as an Optuna distribution
  declares it), checked at `application/bench/children.py::admission_failures` and stated in L1's
  schema; one `algorithm` member proposes numbers with no model call. *Closes:* a `top_k` or
  guidance-scale axis searched as a number.
- **R6 Output refs, judged where the output is.** A row may carry an output ref — hash, media type,
  size, never the bytes; the judge runs where the output lives, inside the pulled program, and enters
  the scorer id with the program's revision; an image-capable proposer reads a bounded preview through
  one injection renderer. *Closes:* a non-text campaign separates from an equal-budget resample arm
  on the held-out bench, graded by a scorer of another family than the loop's judge.
- **R7 One priced count.** `WebSpendBound` is renamed `PricedCountBound` (`item`, `count`,
  `usd_per_item`): a GPU-second, an image, a simulator step or a connector phase's CPU-hour is a new
  item, never a new `NodeSpendBound` kind. *Closes:* GPU-seconds beside tokens in one `SpendRollup`.

**Holds a surge**
- **R8 The surge rehearsal.** The offline world driven by many synthetic accounts on a box-shaped
  install, reading which bound binds first — the provider window, the run queue, memory, disk, open
  streams ([`../operations/access-model.md`](../operations/access-model.md) § What bounds resource
  use); the first is fixed in the repo, never on the machine. *Closes:* the rehearsal ends on a
  refusal the box serves — never an OOM, a 5xx or a dropped stream.
- **R9 The image.** One OCI image built in CI; a deploy pulls a tag and a rollback the previous one,
  never a checkout reset in place.
- **R10 The box without its operator.** Restart and rollback with no interactive sudo; health pushed
  through the outbound admin channel ([`../decisions.md`](../decisions.md) § 0004); a restore drill.
  *Closes:* a deploy and a rollback with no one at the box.
- **R11 Scale-out.** Pulled programs first (evaluation leaves the box), then boxes sharded by tenant —
  a tenant's tree is self-contained, the identity zone the one shared part — never a shared database.

**Maintained by many**
- **R12 The conformance kit.** `gate.py --only optimizer-plugin`'s out-of-tree check, shipped as a
  pytest plugin over the offline world for each plugin kind and the pulled program. *Closes:* a
  connector, a judge and a pulled program from other repositories pass it and are listed, with no
  maintainer edit here.
- **R13 Every request routed to an answer.** Issue forms map "connect X" onto five answers — a
  provider line (R4), a declaration plus a pulled program (R2), a judge (R6), a manifest
  (`examples/optimizer-plugin/`), a Harbor task — and one that fits none is the §0 flag
  [`../architecture.md`](../architecture.md) names. One issue per R-item collects demand.
- **R14 Review past one person.** CODEOWNERS follows the layer `CLAUDE.md` files; a second
  maintainer's first rights are `examples/` and the extras, outside core.

**Ecosystems — each lands on one of R13's answers, none in core:**
- *Model hosts:* NIM, Dynamo (past 1.0, NVIDIA's own serving stack), Triton, vLLM, SGLang, Ollama and the clouds' OpenAI-compatible
  endpoints are R4 lines; the hosted tier serves tenants through vLLM or SGLang. Nemotron's reasoning
  switch (`enable_thinking`, effort) rides `chat_template_kwargs` — a model-profile fact, never a
  node key.
- *Agent frameworks:* LangGraph, the OpenAI Agents SDK, Google ADK, CrewAI, LlamaIndex and
  Microsoft Agent Framework (past 1.0, its maker's successor to Semantic Kernel and AutoGen) get one
  reference pulled program each in `examples/`, each its own
  distribution; NeMo Guardrails' prompts reach it the same way. A framework joins only as a
  standard — adopted by developers and committed to by its maker; a new product stays in
  [`../research/landscape.md`](../research/landscape.md) until it is one.
- *Workflow builders:* Langflow is the headline setup; n8n (its templates ship in
  `connectors/resources/`) gets a pulled-program node on the TypeScript client, Dify a plugin on the
  Python one.
- *Observability and registries:* Langfuse, MLflow, LangSmith and Phoenix — traces in, winners out.
- *Environments:* Harbor's task format, which verifiers, NeMo Gym and the Hugging Face Hub already
  read (0.x, admitted because `connectors/harbor.py` already runs it); R-items add no environment
  seam beside `connectors/harbor.py`.
- *Sandboxes:* NVIDIA OpenShell, under the headline's condition, enters at the containerized cell:
  one Harbor environment class, contributed upstream (Harbor loads any class by `import_path` until
  then) and picked by the cell's existing `environment` key; `connectors/harbor.py` passes any
  environment class, and its compose overlay and image reuse serve every one. An agent already inside
  an OpenShell sandbox is a pulled program (R2) once its network policy admits the API host.
  *Closes:* a Harbor campaign whose cells run in OpenShell sandboxes, set by `environment` alone.
- *Non-text targets* take the evidence order; GR00T's instructions sit with the VLA paraphrases it
  ranks weakest.

**Thin surfaces on R1–R3, each riding a verb that exists:** MCP (C5), a running campaign as an MCP
Task; a GitHub Action running a head-to-head between `main`'s configuration and a PR's and
commenting the paired lift; model migration — a campaign on the new model whose origin is the last
winner; traces in (OTel GenAI JSONL first) beside § What PromptPotter emits.

**Free compute — spend it where calls bind.** Through R4 an open-weight model on free GPUs answers on
no provider's bill (its rate may be zero; R7 meters the GPU-seconds). First the models the instruments
already run — another host is another instrument, so screen its agreement with the cloud host on one
panel before a campaign moves; then the call-heavy rungs: L4 inner cycles, the preprint's seeds and budget
points, the optimizer-model screen (§ Optimizer tuning — the standing board, step 3); then R6's
exhibit, which needs the GPUs themselves.

**Further out:** the archive as fine-tuning data — graded outputs per configuration, handed to a
trainer; an edge device as a pulled program; a hosted media judge on uploaded outputs.

## Connectors

- **Prompt-injection Phase 2:** `TrustedText`/`UntrustedText` renderer types, L1/critique
  injection-echo validators, a repeat-detection circuit breaker.

### The connector as a unit

Operator direction, not yet a design. **The operator picks a connector, not a backend URL**: each
registered connector shows its status, its pipelines and their modes (Harbor's skill delivery is
advanced or primitive).
- **One declaration schema**: `Connector` (`connectors/protocol.py`) served by `GET /connectors` as a
  generated type, and the webapp draws every connector from that alone; nothing branches on a name.
- **The chat hero is the connector's picture**, connector ⊃ pipeline ⊃ node; where you click picks the level.
  Advanced mode draws the skill as a tool attached to the agent, served from `Connector.prompt_delivery`.
- **The pipeline choice rides the existing channel** (`EditDraftPatch.config`'s `exclude_nodes` → … → TermNorm's
  `steps`); `Connector.default_pipeline` widens into a named set with one default.
- **A mode is a node-config key with declared options**, never a search axis, always in identity.
- **Check-in adds no `EditDraftPatch` field**; owed: validation against the declarations, and
  `draft_from_dataset` accepting a verifier-graded dataset. A running campaign never switches in place.

Order: (2) `Connector` declarations + `GET /connectors`, status from `Connector.preflight`; (3) the
TermNorm toggle moves into the connector panel; (4) the dashboard inspector reads the same object;
(5) the C5 write side rides the same overlays, and `change-pipeline-param` is re-scoped or deleted.

## Agent-tool parity

An operating agent calls PromptPotter as one tool: the MCP server exposes mint, run, supervise and
read. **Held on the inbound credential** — owned by [`../developer/stable-api.md`](../developer/stable-api.md)
§ 8; R1 (§ Embedding (M15)) is that credential. The first caller is HuggingChat: a user-added server carries only a name and URL, so ours
authenticates without headers; results are terse named fields, never a document; `run` returns an id
at once and a status tool reads it. The tool set scopes along [`../decisions.md`](../decisions.md)
§ 0005's capabilities; `purge-cold` stays out.

**Coding-agent optimization is deferred** until we have an expert or the field converges.

## What PromptPotter emits

**We write a file and provide a reader; we never load, host, route or hot-swap.** DSPy is the
reference read before designing any ecosystem expansion. The artifact and its reader contract are
[`../developer/stable-api.md`](../developer/stable-api.md) § 5c.

- **`to_dspy`** on the `[dspy]` extra beside `presentation/teleprompter.py`: applies the winner to a
  live program rather than emitting DSPy state.
- **Langfuse write-back** to Prompt Management (the MLflow half exists — [`../operations/observability.md`](../operations/observability.md) § MLflow).
- **A DSPy listing PR** pointing users at `PromptPotterOpt` beside `dspy.GEPA`.
- MLflow as a second viewer: [`mlflow-interop.md`](mlflow-interop.md).

## Judged and turn-structured scoring

What is live is [`../../promptpotter/application/scoring/judges/CLAUDE.md`](../../promptpotter/application/scoring/judges/CLAUDE.md)'s. A
rubric must vary across arms on the same cell before a campaign funds it. Open:
- **Per-step difficulty**: δ and `a` per step under a testlet model (a person×testlet effect; the cell
  stays the atom, since a turn is not an item), gated on identifiability at ~20–34 cells a round, a fixed
  semantic step schema and a new `RulerScope`; it needs each step's term banked as its own `pipeline_data`
  key, never collapsed to a scalar.
- **A declared step as a NODE**, so `campaign_config.exclude_nodes` is the step selector (generalizes
  `connectors/harbor.py`'s `AGENT_NODE`); not running later steps (`_unscoreable_step` raises on a truncated one).

## Evolving agent harnesses

The artifact becomes an agent harness, through `connectors/harbor.py`. Do not re-open "whose
harness": Harbor sits below every harness. The target is a **skilled agent** — many skills, each
maintained and measured separately; measured lift is the only admission ticket.
- **Tier 1 validator** — a `validators/l1_strict.py` rule rejecting a skill body over 500 lines
  before it runs; today only the L2 prompt names the bound.
- **The no-skill arm beside the search** inside one cycle.
- **Skill routing** before the library reaches a few dozen skills.

## Small open items

- **Schema axes** — contrast `description` prose and `response_format` on `justlogic/d234` via
  `evidence --grid`; promote at `proxy_lift_corr ≥ 0.6`, a negative result reverts the axis.
- **Fitness comparability** — the cross-round headline surfaces and the lineage `/N` badge.
- **Exit gate** — `rounds_to_separable ≤ 5` on `llm_only` AND TermNorm under one `l1_generate_hash`;
  `behavior_pass_rate = 1.0` seeded; `proxy_lift_corr ≥ 0.6` over ≥4 paired branches. The clock is
  separability, never an absolute accuracy.
- **Run admission** — look-ahead's ceiling is a hand-set constant per connector, not measured RSS
  headroom; a horizon priced in expected waste would launch deeper.
- **Cheap top-up** — a second `PanelTrigger`, blocked on the rule that prices a missing row against
  the round's marginal draw cell.
- **Origin-relative weighting** — each archive observation weighted by its producing candidate's
  similarity and recency; `Observation` carries a timestamp and lineage hint once archive-wide drift or
  a per-node capability curve is chosen.
- **Delegation, open** ([`../decisions.md`](../decisions.md) § 0005): who edits grants; how a
  channel is identified unspoofably; whether the user's own assistant is attenuable; depth beyond
  one level; whether a delegate is its own tenant.

## Lineage mask

The read side and the fork write side are [`../operations/mask-projection.md`](../operations/mask-projection.md)'s.
**Open (C8): the sample-set half.** A mask that changes which samples a round buys needs a replayable
measurement order seeded into the fork, which `CycleSeed` does not carry; until then a sample-subset
mask previews and stops.

## First-run exhibit

The demo: write down ten films you like, get better recommendations than the starting prompt, and
the prompt. Bars: most people do it; platform-independent; no private data; a right answer
(held-out titles, scored by `list_rr`). Before a round, compute the chance floor of the draw — the
first cut's origin read below it. **MovieLens cannot ship** (no commercial use or redistribution):
collect our own rows, cut under a name nothing has run before. Served from an install-tier campaign
root behind a `demo_mode_enabled` reader.

## Anonymous preview tier

Do not build before the chat backend exists.
- **Its own issuer sentinel, `is_anonymous` asked before the operator arm** —
  `default_claim.py::is_host` reads a missing issuer as the box operator.
- **Empty capability set**: it converses and reads; every action is a sign-in prompt.
- **One shared tenant** (a tenant per visitor is an unauthenticated directory-creation surface).
- **A global daily pool is the ceiling**; a per-visitor cap is UX.
- **No trustworthy origin signal ⇒ refuse the request.**

## Prompt-field order

Today a node's field order is its declaration. Proposal: the check-in carries an order per prompt
node beside its fields (`DraftFile.origin_prompts`), the editor drags it, `edit-draft-campaign`
persists it, a fork inherits it. **Open (operator): an optimizer axis or an operator setting?** Held
is the smaller change. Reorder re-keys every cell, so order is a searchpoint property (never
`localStorage`), and a descendant reading under a changed order renders `?`. The drag surface: a new
`components/ui/` primitive on Pointer Events, keyboard parity (`↑`/`↓` with an `aria-live`
announcement), commit on drop, the handle the touch target.

## Arrangeable workspace

The dashboard becomes a workspace of panels the operator arranges: resize, collapse, pop out, dock
back — Claude Code's cloud workspace for the mechanism, Prime Intellect Lab's run page for the card
look, the operator's Stitch prototype of a draggable heading. On a phone panels stack, resize is
height alone, and a popped panel is a full-screen sheet. Landed: no canvas paints detail over its
marks (the canvas rule in `webapp/components/CLAUDE.md`); Grade sits on the Leader
(`shell/searchpoint/BenchGradeAction`), the masthead's figures read-only. Owed, in order:

1. **One view, one panel.** Each family in `components/dashboard/layout/panes.tsx` renders one frame;
   Candidates hosts three today (chart, forest, inspector), which become regions of one panel.
2. **The arrangement is data** — order, size and docked/popped per view ride the address beside the
   open views (`lib/workspace.tsx`), never a second store.
3. **Panel chrome is one `components/ui/` primitive**: the header row is the drag handle, an edge
   resizes, collapse and pop-out sit in the header; Pointer Events with keyboard parity, the same
   drag surface § Prompt-field order needs.
4. **The tapped candidate's before/after** — its diff and flips against the origin in the inspector:
   `SubjectBox` splits into the leader's picker and the subject reading the inspector hosts.

Closes when the operator arranges the dashboard on a phone and a desktop, the arrangement survives a
reload, and a popped panel docks back.

## Run card

The run card is the chat's sample walk alone: the leader and the heatmap are the dashboard's, the target pipeline the chat hero's — no block renders in both views. Owed: the round
that changed each key, served ("changed in C7.3"), on the leader's diff. Rejected: the round rail of
hollow/filled bars, the ring readout, and a second card mirroring the dashboard behind `more`.
