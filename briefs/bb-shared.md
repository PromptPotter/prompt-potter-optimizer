# backbone phase: what every bb chain shares (read beside `briefs/common.md`)

**The unit of work is a MOVE of the approved end state**, not a debt area. `briefs/backbone.md` is the
merged backbone design the operator approved on 2026-11-28. Your brief names one move (N0–N14, W). Its row
in `backbone.md` § 4 (root, whole file set, wipe/re-key, size), the rows § 8 maps to it, and its
verification lines in § 5 are your spec. The debt rows are indicators. What closes a move is its root
being gone from the code; the rows its root dissolves close with it. `briefs/backbone-topdown.md` and
`briefs/backbone-bottomup.md` are its two inputs, to be read only where `backbone.md` cites them.

**Settled; never reopen (operator, 2026-11-28):**
- A member's behaviour key keys an import by the names it binds, never by module path.
- A plugin imports the contract (`optimizer_contract.py`), `domain`, and the rank-G grading modules. It
  imports nothing under `bench/`, `runner/`, `initialization/` or a built-in's package, and there are no
  re-exports.
- `application/` keeps flat packages; the import ladder (topdown § 0) orders them.
- Adoption is a connector `publish` behind `adopt`. A group budget draws down one group hold, origins
  first. A round closes at its election. The engine ships only `retune`. The model catalogue is a
  host-edited file. Before W wipes, a landing that drops accounts' run data releases their charges and
  posts the notice (option (a)).

**The member fence, defined once here.** A member (`application/optimizers/**`) and
`examples/optimizer-plugin` may import only:
- `shared` and `domain` (a setting a member reads from `config` reaches it through the face, so a
  `config` import counts as an edge);
- the rank-G grading modules (`scoring/formula/`, `scoring/judges/`, `scoring/evaluators.py`,
  `scoring/paired.py`, `scoring/race_protocol.py`);
- the contract modules: `optimizer_contract.py`, `optimizer_knobs.py` and `optimizer_identity.py`.

Inside a built-in's package, its own modules are allowed too. Anything else is an edge against the
ladder. Where `backbone.md` differs from topdown § 0's rank map (here `optimizer_identity.py`, which § 0
ranks A1), `backbone.md` wins.

**BASE:** `origin/release/next` at `89e4cd808` (wave 11 regrouped, 13 commits over `main`). Report
`git log -1 origin/release/next`. `briefs/common.md`'s BASE FIRST ancestor check is against
`origin/release/next`, not `origin/main`. The shas in `backbone.md` (`ff429d376`) name the tree before the
regroup. Its code equals `89e4cd808`'s, so read its `file:line` citations at `89e4cd808`.

**Whole file set, across areas.** Your move's file set in § 4 is yours, whatever area a file sits in. A
file outside it is touched only as a minimal re-point (an import, a call site of a symbol you moved, a
comment naming it), and each one is named in the commit body. If the root needs a file another running
chain owns, cut that link and file one line; never cut the root in half.

**Running at the same time (first batch):** `bb-ladder` (N0), `bb-prune` (N1), `bb-fence` (N2+N3),
`bb-commands` (N12), and `bb-folds` (a read-only measurement). Their file sets are disjoint, except that
each edits its own `BASELINE` lines in `scripts/complexity_ledger.py` and its own rows in the debt doc.
The orchestrator resolves those at landing.

**Tests:** add a fact for what you change, in `tests/CLAUDE.md`'s tier: a pure function where the rule is
decided, else through an existing door of `scripts/offline_world.py`. Edit a fact whose assertion your
change moves. Delete a fact only together with the code it tested.

**Carried from `briefs/w10-shared.md`, unchanged:**
- A re-key never defers a fix; name each behaviour-key move old→new with its mechanism.
- BASELINE is set to the measured count, with its reason.
- Generated files are regenerated, never hand-edited.
- Hubs: edit only your own row, block or field.
- Run data may be wiped; name each reshaped record. A SURVIVOR row that moves ships a one-shot migration.
- The roadmap stays untouched.
- A fork your brief does not decide is decided along the root and named under "decided in-run".
- Commit messages stay under ~1000 characters.
- Never CSS anchor positioning.

**No lone small commit.** A row you file, a wording fix or a doc line rides the commit of its link.

**Report, beyond common.md's:** the move's measured size against its § 4 estimate (package lines
+added/−deleted), the ladder count and `type_cycles` before and after, and every claim of § 5 or § 10 your
move tested, marked held or refuted with its command.
