# w11-optim2: area 09, round 2: the backbone owns crown, hold and the stop signal (cloud · Opus)

`briefs/common.md`, `briefs/w10-shared.md` and `briefs/w11-shared.md` bind you. SPEC: `briefs/map.md`
§ 09 and the debt doc's `## 09` section (8 rows). w11-plugin moved the plugin contract below the bench.
This round makes the peers thin.

Target:
- Every optimizer is a manifest whose knobs are its nodes' `config`.
- Its working state rides the round as `optimizer_state`.
- One owner per rule and per cap.
- A peer supplies a ranking; the backbone owns crown, hold and the stop signal.

Links, deletion first. Work each row along its root:
1. **Selection and the race are written once per peer, not in the backbone**. The backbone takes a score
   map and owns the leader, the tie rule, the `RoundEnding`/`Selection`, the `eliminated` `StopSignal`
   and the shared-cell mean. capo, gepa and levi each supply a ranking. This is the deletion-heavy link:
   measure the peers' lines before and after.
2. **A held round's words are the election match's fall-through**. A selector's ending type is the endings
   it can decide. This link edits `RoundDecision.ending`'s type in `domain/`, so land link 1 on
   w11-records2's round envelope as it is on your base.
3. **Peers hold defaults and an Optional the manifest should own**. A knob is a node `config` field, and
   the round carries what exists.
4. **Potter's dispatch spells a rule twice and a cap inline**. One owner per rule and per cap. A
   whole-candidate verdict names no axis.
5. **`theta_election` names a θ it no longer reads**. Rename it to what it decides, with its doc row. It
   is a behaviour-key move: name old → new.
6. **A coupling note is developer prose on every potter campaign**. An info a run keeps is no news, or
   it is one operator sentence.

Leave open, each for its reason: **"slot" survives in the text an LLM reads** and **`l1_critique` on
minimax runs a cluster's id list to `max_tokens`**. Both are prompt edits, measured by a paid comparison
(tune 1).

Census: `application/optimizers/**`. Give each module with no row a verdict. Then answer: what does a
third-party optimizer still have to edit here?

OWNS: `application/optimizers/**`, `application/optimizer_contract.py`, `application/optimizer_manifest.py`,
`application/scoring/race_protocol.py` where the backbone takes the race, `application/scoring/row_policy.py`
and `domain/crowning_bar.py` for link 5's rename, `assets/optimizers/*/pipeline.yaml` (link 3's fields and
link 5's rename), and their tests. In `domain/` you edit only `RoundDecision.ending`'s type (link 2) and
`domain/failures.py`'s docstring (link 4; it was `wounds.py`).

w11-config2 runs at the same time and retypes the node document's parse. It edits no optimizer
`pipeline.yaml`, so a knob you move there is yours alone.

DONE WHEN:
- Each row is re-tested through a door, before and after.
- Every peer's leg moves only where a row names the move; equals-main names each one.
- The keys are named.
- `python scripts/gate.py` is green.
