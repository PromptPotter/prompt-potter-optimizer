# Refactor arcs — sizing, landing, briefing, reviewing, stopping

A refactor arc closes rows of [`../specs/code-debt-cleanup.md`](../specs/code-debt-cleanup.md) in the
order of [`../specs/roadmap.md`](../specs/roadmap.md) § The cleanup chains (audit, 2026-11-10). This
page owns how an arc is run, whether by a person or by agents.

## Size and scope

- **A link is one decision**, with any number of mechanical sites, and a few hundred changed lines at
  most (operator, 2026-11-07). A chain of links is one session; chains are ordered by mechanism, root
  first. Split a bigger link before dispatch.
- **A census comes before the briefs**, one per set of chains up to their landing. Every holder of each
  fact a link touches is listed repo-wide. A row whose holders reach past the link's files moves the
  link's edge or waits for a chain that owns them.
- **No new audit pass until the backbone chains land** (M3, K0, DOORS, M2, M4, CONN; operator,
  2026-11-07). A row filed in passing names its chain in **Rides with:**, or carries a **Held:**
  reason.
- **No arc holds a command's state anywhere but the account's command log.**
- **No arc adds a cadence, a `revalidate` list or a cross-invalidation** beyond batch E's before FEED
  lands; a new read polls on the descriptor it has, and A4's conversation opens no second `EventSource`.
- **Scope, and every decision the arc needs, are settled before the first build.** A decision that
  surfaces mid-arc is asked before the next build, never built ahead of the answer.
- **An arc owns its files.** Arcs that run together touch disjoint files, or name the one they share.
  Arcs whose files overlap run as a chain: each link branches off the previous one's tip, and only
  disjoint chains run in parallel, two or three at a time.
- **Deletions go first.** An arc that removes a second door, a shim, a dead field or layer, or a test
  the charter rejects runs before an arc that reshapes. A deletion is quick to prove.

## Landing

- **An arc joins its landing on proof, not on review.** A landing is one `batch/<n>` branch holding the
  chains of one census. The proof is the full gate green on the arc's tip, plus the brief's
  equals-main checks: the optimizer treatment digests and decisions ([`offline-run.md`](offline-run.md))
  and the served responses. No review is waited for.
- **A row that moves a treatment digest lands with the chain that owns its file**, named in its brief,
  with decisions and requests equal to main, any time before the next paid run (operator, 2026-11-07).
  No re-key arc gathers them.
- **One independent review covers each landing, before it reaches main** (operator, 2026-11-07). A
  risky link closes its landing early. The review reads the landing's combined diff against main.
  Then one fix pass on the batch branch fixes every INTRODUCED finding, and each PRE-EXISTING one
  becomes a ledger row. The landing goes to main on the push gate's proof, with no second review
  round.
- **A standing problem is accepted while it does not escalate.** The ledger is worked to its end,
  then passed again.

## The brief

Every brief carries the following:

1. **Read first:**
   - the root `CLAUDE.md` § Non-negotiables;
   - [`../architecture.md`](../architecture.md) §0 + §0.5;
   - the `CLAUDE.md` of each layer touched;
   - [`../../tests/CLAUDE.md`](../../tests/CLAUDE.md) before a test;
   - the arc's rows in the code-debt ledger, which are its spec.
2. **Owns:** the files it may change.
3. **The changes it names.** Everything else equals main:
   - the wire;
   - the optimizer treatment digests ([`offline-run.md`](offline-run.md));
   - the served responses;
   - the read cadences.

   A named change whose root needs a mechanism outside the owned files is cut at first sight and
   filed as a row, never built in.
4. **The repo's rules:**
   - ship the root, with no backward compatibility;
   - all five entry points reach a changed capability;
   - delete every debt row the arc ships, and fix every doc it makes stale.
5. **Proof:** `python scripts/gate.py --changed` while iterating, then the full gate before pushing.
   The `surface-ledger` check rises only with its reason in the commit body.
6. **Git:** one squashed commit per arc on an `arc/<id>` branch. Never force-push, never
   `--no-verify`. Commit text is technical only.
7. **Report** (≤500 words):
   - what went;
   - what stayed, and what varies in it;
   - each re-test count, before and after;
   - the gate result;
   - shared files touched;
   - rows to file.

## The review

- **Independent and read-only.** It gets the briefs and the batch's diff, never a builder's report.
- **It reads the landing against main**: the arcs that joined it since main. Its depth follows the
  link: a link that adds a mechanism gets a deep read, a pure deletion a re-grep of the holders.
- **Every changed capability gets a five-door row**: CLI, `/potter-run`, `embedded_run`, REST and the
  webapp, each reached or named a carve-out.
- **It finds, ranked:**
  1. behaviour moved that the brief did not name (checked against the equals-main list);
  2. transition code: a shim, a kept bridge, a reader of two shapes;
  3. incomplete excision: a re-test count not at its target, a second door still standing;
  4. edits outside the owned files;
  5. stale docs and colliding names;
  6. a shallow module: what its callers must know, which call order, errors and configuration it
     could handle itself, and whether the code gets simpler with it deleted. A new seam needs
     something that actually varies.
- **Each finding gives** file:line, the defect, and a concrete failure scenario. It is tagged
  INTRODUCED or PRE-EXISTING; check with `git show origin/main:<path>`. An INTRODUCED finding names
  the invariant it breaks and the test that would hold it.
- **A census lists every holder of each fact the arc touches**, repo-wide, each tagged in-layer or
  out-of-layer. Out-of-layer holders come with a one-line row text.
- **A review's root fix is a finding.** The next brief is written from it, never copied from it.

## Fix passes and stopping

- **A fix arc names each finding's invariant**, fixes every in-layer holder the census lists, and
  adds what holds it: reverting the fix fails it. That is a test only where
  [`../../tests/CLAUDE.md`](../../tests/CLAUDE.md)'s charter admits one. Otherwise it is a check in
  the owning module, or the code's own shape. A failure that a re-run fixes gets no test.
- **A batch gets one fix pass**, never a second. Whatever the pass cannot close becomes a row.
- **Arcs that conflict get one fix pass.** It runs on their merge (`git merge-tree` names the
  conflicts), in one session that resolves the seam and fixes every arc's findings. It never
  runs as parallel passes that collide again at assembly.
- **A build that fails its proof** gets one more build in the same session. A second failure stops
  the arc, and the operator cuts or re-scopes it.
- **Stop early:**
  - a mechanism whose every fix finds a new state is cut back, not hardened;
  - a written diagnosis that hands a mechanism to another arc stops work on it that turn.
- **A stopped or cut arc's tip is tagged `salvage/<id>`.** The tag is annotated with what its
  reviews found clean and what must not be ported. Later small arcs port from it piece by piece
  (`git tag -l 'salvage/*' -n9`). Never delete a `salvage/` tag or its branch before its rows land.
  The last link to land deletes both.

**Why.** A batch of five arcs (2026-11-05) spent most of about 48 builds on work it later cut. Three
of those arcs carried changes that needed another arc's mechanism first. None checked "equals main"
until about its sixth round. Each review re-read the whole re-squashed arc instead of the delta. Wave 1 (2026-11-06) ran eight
medium arcs in parallel. None converged in round 1, and six needed a mechanism outside their own
files. Three tripped the stop rule in round 2. The one arc re-cut to about ten files converged and
landed. The first small arc after it (M1a, 2026-11-07) still spent a second review round on a
Windows Ctrl+C edge case. So the operator moved review from each arc to each batch of up to five,
still before main. Wave 2 (2026-11-07) landed nine batches and moved no backbone re-test: "about ten
files" held a 15-site row out of three censuses, and the digest's over-reach parked every row it
touched. So a link is sized by its decision, the digest reach (K0) goes first, and review moved to
each landing.
