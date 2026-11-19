# Review: exhibit-acts (a served read names the acts its reader may take)

Reviewed: the diff `exhibit_acts_on_main.diff` (30 files, +448/−123), as applied in
`<path>`.
Read against `briefs/exhibit-acts.md` and `briefs/common.md`. I did not see the builder's report.

Probes are in `<path>`:
- `exa_probe.py`: old rule beside new, per reader.
- `exa_probe2.py`: ETag version per reader.
- `partial_caps.py`: partial-capability readers.
- `proposed_rule.py`: the fix proposed in #5, run against the landing.

## Verdict: LAND AFTER FIXES

The core is right. `command_kinds.py::filable(identity)` reads the map the dispatcher gates on
(`CommandKind.capability` through `has_capability`). `admission.py::admitted` applies it to the
read's own `stores.identity`, so the exhibit's capability-less reader gets `offers: null` and
`read_only`. In the webapp, the dock, the critical line's Pause, the decision buttons, stub
cleanup, limits, lookahead and skip all go dark for that reader, and stay dark when the path is
pinned. The follower still sees the readouts: the critical line, "Samples in flight" with
`la.flight`, and the locked caps.

Seven defects block main:
- one reproduces the row's own symptom (an offered act that 404s);
- one breaks the "one rule" under HTTP caching;
- one leaves the backend proof vacuous.

### What I ran (green on wt-exa unless noted)
- **Backend tests:** `pytest tests/test_security.py -k "exhibit or follower or admission or refusal_said"` → 6 passed. Imports resolved to wt-exa (`PYTHONPATH` pinned, checked by `__file__`).
- **Webapp tests:** `npx vitest run` on `components/shell/__tests__/exhibit-acts.test.tsx`, `shell/remote/__tests__/RunLimitsControl.test.tsx` and `searchpoint/__tests__/press-subject.test.tsx` → 8 passed.
- **Static checks:** `npx tsc --noEmit` is clean. `ruff check`, `ruff format --check` and `mypy` on the changed Python modules are clean.
- **Specs:** `scripts/build_specs.py::openapi()`, regenerated in memory, matches `docs/specs/openapi.generated.json` byte for byte. `SERVED_CONTRACT` 6cac0d3589aee814 → 069adfec2449c39e.
- **Complexity ledger:** `complexity_ledger.py` (print mode) gives `package_lines` 112176 on main and 112241 here. +65 is correct; the baseline bump of +43 absorbs main's 22 lines of slack. `promptpotter/` is +130/−65, and about 46 of the +130 are the `RunAdmission` move.
- **The new webapp test against the PRE-diff components:** I made a scratch copy with the 12 component files reverted to main. The follower case FAILS at `exhibit-acts.test.tsx:100`, so the test does catch the regression. The copy has since been removed; main's `node_modules` was untouched.
- **Not run:** the full gate, the gate's `layering` and `eslint` checks (layering was read, see Q5), and the brief's equals-main and `offline_run.py --digests` proof. Nothing here supports a claim that decisions or digests are unmoved.

### Answers to the six asked checks
1. **Same rule?** The same map, not the dispatcher's whole rule.
   - `filable` reads `CommandKind.capability`.
   - `_rung`'s cancel branch (the filed command's rung) and `pressed_at(door)` sit outside it, though its docstring cites `_rung`. This is harmless today (L6).
   - **Offered but refused:** yes. Through the compare pane, #1.
   - **Refused but would be accepted:** yes. A reader who files a campaign refusal's remedy loses it, #5.
2. **Every act field reaches the rule?**
   - Server-filtered: `RunAdmission.offers`, `refusal` and `payload`; `CommandReading.next_acts` and `holds`.
   - Joined on the client: decision `actions`, `facts.verify`, `bench.status.offered`, `deletable_stubs`, `skip_refusal`.
   - No served offer at all, gated on `filable` in the client: limits, lookahead, fork, scenario, snippet.
   - Missed:
     - the stop note's "Next: …" (#6);
     - the compare pane's subject (#1);
     - `CampaignMenu` and the account tabs, for delegates (L4);
     - MCP `list_tools` (L5).
3. **Client gate on a follow, pin or role?** None. I grepped `webapp/components` for `pinned`, `FOLLOWING`, `following`, `access_state`, `.host` and `exhibit`.
   - `pinned` appears only in the critical line's gone-cause wording and the masthead's "follow your run" link.
   - `access_state` appears only in the onboarding gates.
   - No act's visibility reads any of them.
   - A follower still sees the readouts, except the apply-scenario readout (#4).
4. **Root-fix rules:** no shim, fallback or breadcrumb. One flag steers a callee (#7). Two mechanisms carry one rule (L1). A name collides (Symptoms).
5. **The `phases.py` → `hierarchy.py` move:** a clean move with one owner.
   - There is one `RunAdmission` definition (`domain/hierarchy.py:102`), and no importer of `phases.RunAdmission` remains anywhere (grep over `promptpotter`, `tests`, `scripts` and docs).
   - `hierarchy.py` imports only `domain.*` and `shared.errors`. `command_kinds.py` adds only `shared.identity`. No domain module imports application (read against `gate.py:193`'s layering table, not run).
   - The reason for the move holds: `command_kinds` imports `phases`, so `phases` cannot type `filable: list[CommandKind]`.
6. **Tests:**
   - The webapp test fails on the old components (shown above).
   - The backend test cannot fail on the old offer (#3).

---

## Must fix before main

### 1. The compare pane offers Verify and Steer-fork on the exhibit, and each 404s
`webapp/components/searchpoint/VerifyAction.tsx:58-62` and `SteerForkAction.tsx:19` gate on
`useViewedCycle()`, the workspace's viewed cycle. Both file on their `path` prop.

How a channel reaches them:
- `components/compare/ChannelDetail.tsx:218-226` passes `picked.path`.
- `channel-hooks.ts:124` sets `path = pathOfAddress(selected.address)`, which is the channel's own campaign.
- A comparison spans campaigns (`lib/state/compare-selection.ts:1`).
- The candidates card adds the viewed candidate, the exhibit's included, as a channel (`components/candidates/useCandidatesModel.ts:59-64,183`).

- **Failure:** a follower adds an exhibit candidate to compare, then pins one of their own campaigns. The exhibit channel's Verify and Steer-fork read the own campaign's `filable` (all kinds), so they render. Each press goes to the caller's stores and 404s. This is the row's symptom, reached by a pin.
- **The reverse also happens:** while the follower views the exhibit, their own channels lose both acts. SteerForkAction had no viewed-cycle read on main, so this is new.
- **Verified by:** reading the code at the lines above. I did not render it.
- **Root fix:** the gate reads the admission of the subject the act files on, the cycle at `path` in that channel's own tree. `VerifyAction`'s `facts.verify` has the same viewed-cycle mismatch on main, and the same fix closes it.

### 2. The ETag version ignores the reader, but the body now depends on it
`application/hierarchy.py:553-578::entries_version` keys on `identity.user_id`, never on
`identity.capabilities`. All three reads that carry `RunAdmission` hash it into their ETag
(`presentation/api/routers/reads.py:123-142`):
- `_hierarchy` through `hierarchy_version`;
- `_subtree` through `hierarchy_subtree.py:670-681::subtree_version`, which is `(*entries_version(held), *passes)`;
- `_leaf_store` directly.

`exhibit_stores` builds the follower's reader as `default_identity(tenant_id=host)`, which has
the host's `user_id`.

- **Measured:** `exa_probe2.py` on the new test printed the same version digest `5c70901229e0` for the follower's reads (`filable` 0) and the host's reads (`filable` 36).
- **Failure:** a grant narrowed or widened through `/grant`, or a token's scope change, moves no store. The next poll is a 304, and the client keeps the old `filable`, `offers` and `refusal`. Any client holding the other reader's ETag for the same URL is also answered unread. `_conditional`'s own docstring names it: "an input … left out of *version* goes stale".
- **Constraint for the fix:** the version covers every input of the body, so it must cover the reader's capabilities. Placement is the builder's call. `entries_version` is also the version the feed names (`application/cycle_reads.py:288`), so folding `sorted(identity.capabilities)` in there moves both together.

### 3. The backend test cannot fail on the old offer, and the verb branch is untested
`tests/test_security.py:1444-1465`. Measured with `exa_probe.py`: the fixture's campaign refuses
every launch for every reader (`definition_unread`; I infer from the code, without checking, that
the minted campaign has no readable definition).

- Old rule, follower: `offers=None`, refusal `definition_unread`. New rule, follower: `offers=None`, refusal `read_only`. Host: `offers=None` either way.
- So `assert admission.offers is None or kinds` is vacuous for both readers.
- The host is never offered anything, so the brief's "the host's own read offers them all" is never shown. The row's case, `offers: "pause"` served to the follower, is never reached.
- Only the new field and the new code distinguish (`filable == []`, `code == "read_only"`).
- No test covers:
  - the `RUN_VERB_KIND[...] not in filable` branch (`hierarchy.py:150`);
  - the payload filter (`:160`);
  - `read_command`'s `offered`/`holds` filter (`command_reading.py:174-178,217`).
- **Root fix:** stand the exhibit's cycle in a phase that offers a verb, RUNNING (`pause`) or DETACHED with a readable definition (`start`). Assert the host gets it and the follower gets `offers is None` with `read_only`. Add one case for `read_command` with a scoped reader.

### 4. The apply-scenario gate hides a readout from the follower
`webapp/components/candidates/CandidatesCard.tsx:146` drops the whole `ApplyScenarioPanel` when
`fork-cycle` is not filable. Its body (`ApplyScenarioPanel.tsx:44-69`) is a readout: "the record
and the counterfactual part at round N … never parts from the record". Only the HoverCard and
button at `:76-82` are an act.

- **Brief broken:** "A follower still SEES the exhibit whole … only its acts go."
- **Verified by:** reading the code.
- **Root fix:** gate the button inside the panel and keep the text.

### 5. `read_only` drops a campaign refusal's remedy the reader CAN file
`domain/hierarchy.py:149-153` sets `refusal = _READ_ONLY` if the verb is unfilable, or if any one
act named past the refusal is unfilable (`offered(named, filable) != named`). That discards the
campaign's own refusal and every act past it, including acts the reader may file.

- **Probe** (`partial_caps.py`, `proposed_rule.py`): DETACHED with a campaign refusal naming `mint-campaign` (my own stand-in refusal, not a product message):
  - a `create`-only delegate is served `read_only` with no act, though `mint-campaign` is filable. This is an act refused that the gate would accept, the converse of Q1.
  - A `run` or `run+step+budget` delegate loses why the launch is refused, and reads only "You read this run; acting on it is its owner's.".
- Partial readers are real: `/grant` delegates (`infrastructure/identity/accounts.py:148-168`) and scoped bearers (`:244`).
- **Root fix, probed:** `read_only` stands only where the run asks something (a verb or a named act) and the reader files none of it. Otherwise the run's own refusal stands, with its `next_acts` passed through `offered`.
  - `proposed_rule.py` runs it over 6 readers × 6 run states.
  - It matches the landing for the exhibit's reader in every state: RUNNING, GATE and DETACHED are `read_only`, and QUEUED keeps "Queued". It also matches for the owner.
  - It differs only where a partial reader may take the launch or the remedy: the campaign refusal stands, and the create-only reader keeps `mint-campaign`.

### 6. The stop note still words the stop's acts from a client table
`webapp/lib/derivations/run-phase.ts:65-78` (`stopNote`, `runNextStep`) builds "Next: …" from the
static `STOP_REASON_INFO[...].next_acts`, filtered on the client by `launch_refusal`. The server
computes the same list in `RunAdmission.of` (`hierarchy.py:154`, `acts`), now filtered by
reader, but serves only its `payload`.

- **Failure:** `RunSummaryItem` (`components/chat/RunCard.tsx:26-35`, mounted for every frozen cycle entry in the chat thread at `ChatPane.tsx:92`) shows a follower of a stopped exhibit "Next: Resume; …", while the dock says "You read this run". It is text only, so no 404 is reachable, but two spellings of one list now disagree. The brief forbids a client derivation beside the served fact.
- **Root fix:** `RunAdmission` serves its offered stop acts (the `acts` it already computes) and `stopNote` reads them. That deletes the client filter and leaves `launch_refusal` with no reader, so it goes too.

### 7. `RunLimitsControl` takes a `files` flag from its caller
`webapp/components/shell/remote/RunLimitsControl.tsx:51-55` takes `files: boolean` from
`RemotePanel.tsx:30-35`. The component files `set-limits` itself, so whether its reader may
file it is the callee's to read, as `LiveSegment` and `PromptFieldsEditor` do. Root `CLAUDE.md`
names this shape as a patch: "a callable or flag passed so a caller steers what the callee
should own".

- **Root fix:** `RunLimitsControl` reads the served fact itself. That also reverts the two test edits that only add `files` (`press-subject.test.tsx:122`, `RunLimitsControl.test.tsx:40`).

**Ledger:** the diff deletes the batch/29 row whole. With #1 open, the row's symptom still
reproduces. Close it with these fixes, or cut it to its open half; never delete it as is.

---

## File as ledger rows

**L1. Two mechanisms for one rule.**
- Server-filtered: `RunAdmission.offers`/`refusal`/`payload`, and `CommandReading.next_acts`/`holds`.
- Client-joined against `filable`:
  - `skip_refusal` (`served_dashboard.py:774`);
  - `facts.verify`;
  - `bench.status.offered`;
  - `deletable_stubs` (`hierarchy_subtree.py:382`, in Owns, untouched);
  - `ActivityDecision.actions` (`domain/activity.py:72`).
- No served offer at all: limits, lookahead, apply-scenario, steer-fork, bank-snippet.
- A REST or MCP reader of the exhibit is served `skip_refusal: ""`, `bench.status.offered`, `deletable_stubs` and a `submit-arms` decision.
- The brief asks for the fewest spellings, decided and justified. Today it is 1 server spelling plus about 12 client joins.
- **Action:** each computed offer reads `filable(stores.identity)` where it is built. `filable` stays served only for acts no read offers, or every offer becomes a join. Decide which and count.

**L2. The reader's fact is repeated on every entry.**
- `RunAdmission.filable` lists all 36 kinds. 7 of them carry an `account.*` capability (keys, tokens, settings, terms). Draft, upload, queue and archive kinds are workspace-scoped. No run surface reads either group.
- Measured: one admission's JSON goes from 303 to 993 bytes. That repeats per campaign entry and per cycle on every `/hierarchy`, subtree and dashboard poll.
- "RunAdmission", the launch half of a cycle's run verbs, no longer describes the field.
- **Action:** carry the reader's kinds once per read, and have the webapp read them through one hook.

**L3. Three webapp sources for one fact, ten spellings.**
- Sources: `dash.admission.filable`, `useViewedCycle().state.admission.filable` and `tree.state.admission.filable`.
- Spellings: `.admission.filable.includes(k) ?? false` at about 10 sites.
- **Action:** one hook, keyed by the subject the act files on. This also closes #1.

**L4. Delegate act surfaces with no fact to read.**
- `components/shell/sidebar/CampaignMenu.tsx` (rename, archive, delete: lifecycle).
- `AccountUsageTab.tsx:253,292` (cancel-queued: run; set-concurrent-cycles: budget).
- `ArchiveCompactionControl.tsx` (lifecycle).
- `ApiTokens`, `AccountKeysTab` and `AccountPreferencesTab` (`account.*`, never delegated).
- All of these render for a `/grant` delegate who lacks the capability, and each press 404s. `/auth/me` already serves `capabilities`, but only the profile tab displays them.

**L5. MCP `list_tools` ignores the reader.**
- `presentation/mcp/server.py:180-200` lists every `HOST_TOOLS` command whatever the session identity's capabilities, so a scoped bearer is offered tools that each 404.
- **Action:** filter command tools through `filable(session identity)`. `host_surface.py` is in this brief's Owns.

**L6. `filable`'s docstring cites `_rung`, but `filable` is the map, not the gate's whole rule.**
- `_rung`'s cancel branch and `pressed_at(door)` are outside it.
- It is harmless today: the webapp gates cancel on the filed kind, and the API door presses every kind. The docstring overclaims.

---

## Symptoms (in passing, some off-diff)
- `domain/command_kinds.py:429::offered` collides with `application/datasets/startability.py:58::offered` (a dataset offered, a bool). Root `CLAUDE.md` says names are distinct.
- `domain/phases.py:357::holding` went public. It collides with `application/caller.py:38::Caller.holding`, `domain/optimizer_state.py:122::holding` and `infrastructure/store/read_model.py:966::holding`, and the name does not say it builds a Refusal.
- `webapp/lib/test-fixtures.ts:44-54::OWNER_FILABLE` is a hand-copied list of all 36 kinds. It drifts silently when a kind is added. Generate the kind list into `types.generated.ts`.
- `domain/hierarchy.py:146` still spells `info.action in ("resume", "start")` beside the new `RUN_VERB_KIND`. One map is enough.
- `domain/hierarchy.py:160`: the `offered(acts, filable)` payload filter changes no payload a reader can send. Only `start-run` acts carry a payload, and an unfilable `start-run` already blanks `offers`.
- `application/command_reading.py:178`: a loop-invariant `SUBMIT_ARMS in kinds` inside the comprehension.
- `domain/activity.py:68-69`: owner-voiced copy ("holds for your arms … until you answer it") is served to a follower who cannot answer. Likewise `run_held`'s "Holding for you" for a partial reader.
- `domain/hierarchy.py:99::_READ_ONLY`: "acting on it is its owner's" is false for a delegate who may take other acts (a run-only delegate at RUNNING).
- `VerifyAction.tsx:58-60` already read `useViewedCycle()?.facts.verify` while acting on `path` on main (see #1).
- `PromptFieldsEditor.tsx:101-103` banks nothing while the dashboard loads. It fails closed and re-banks on a later commit, but the owner's bank now waits on a second read.
- The exhibit's reader carries the host's `user_id`, so any key built on `identity.user_id` cannot tell the follower from the host (see #2).
