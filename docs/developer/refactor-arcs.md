# Refactor arcs — sizing, briefing, reviewing, stopping

A refactor arc closes rows of [`../specs/code-debt-cleanup.md`](../specs/code-debt-cleanup.md) in the
order of [`../specs/roadmap.md`](../specs/roadmap.md) § The crunch (operator, 2026-11-04). This page
owns how an arc is run, whether by a person or by agents.

## Size and scope

- **An arc is small: one or two ledger rows, about ten files at most.** Split a bigger one before
  dispatch.
- **A census comes before the brief.** Every holder of each fact the arc touches is listed repo-wide.
  A row whose holders reach past the arc's files moves the arc's edge or waits for an arc that owns
  them.
- **Scope, and every decision the arc needs, are settled before the first build.** A decision that
  surfaces mid-arc is asked before the next build, never built ahead of the answer.
- **An arc owns its files.** Arcs that run together touch disjoint files, or name the one they share.
  Arcs whose files overlap run as a chain: each link branches off the previous one's tip, and only
  disjoint chains run in parallel.

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

- **Independent and read-only.** It gets the brief and the diff, never the builder's report.
- **It reads the delta since the last reviewed tip**; the whole arc is read once, at close.
- **It finds, ranked:**
  1. behaviour moved that the brief did not name (checked against the equals-main list);
  2. transition code: a shim, a kept bridge, a reader of two shapes;
  3. incomplete excision: a re-test count not at its target, a second door still standing;
  4. edits outside the owned files;
  5. stale docs and colliding names.
- **Each finding gives** file:line, the defect, and a concrete failure scenario. It is tagged
  INTRODUCED or PRE-EXISTING; check with `git show origin/main:<path>`. An INTRODUCED finding names
  the invariant it breaks and the test that would hold it.
- **A census lists every holder of each fact the arc touches**, repo-wide, each tagged in-layer or
  out-of-layer. Out-of-layer holders come with a one-line row text.
- **A review's root fix is a finding.** The next brief is written from it, never copied from it.

## Fix passes and stopping

- **A fix pass names each finding's invariant**, fixes every in-layer holder the census lists, and
  adds what holds it: reverting the fix fails it. That is a test only where
  [`../../tests/CLAUDE.md`](../../tests/CLAUDE.md)'s charter admits one. Otherwise it is a check in
  the owning module, or the code's own shape. A failure that a re-run fixes gets no test.
- **A small INTRODUCED finding is fixed in the branch.** A PRE-EXISTING finding becomes a row,
  never another pass.
- **Arcs that conflict get one fix pass.** It runs on their merge (`git merge-tree` names the
  conflicts), in one session that resolves the seam and fixes every arc's findings. It never
  runs as parallel passes that collide again at assembly.
- **An arc has converged** at a round with no INTRODUCED finding.
- **Stop early:**
  - the second round whose lead findings the previous pass introduced stops the arc, and the
    operator cuts or re-scopes it before another build;
  - a mechanism whose every round finds a new state is cut back, not hardened;
  - a written diagnosis that hands a mechanism to another arc stops work on it that turn.
- **A stopped or cut arc's tip is tagged `salvage/<id>`.** The tag is annotated with what its
  reviews found clean and what must not be ported. Later small arcs port from it piece by piece
  (`git tag -l 'salvage/*' -n9`). Never delete a `salvage/` tag or its branch before its rows land.
  The last link to land deletes both.
- **One independent review covers the assembled batch** before its PR.

**Why.** A batch of five arcs (2026-11-05) spent most of about 48 builds on work it later cut. Three
of those arcs carried changes that needed another arc's mechanism first. None checked "equals main"
until about its sixth round. Each review re-read the whole re-squashed arc instead of the delta. Wave 1 (2026-11-06) ran eight
medium arcs in parallel. None converged in round 1, and six needed a mechanism outside their own
files. Three tripped the stop rule in round 2. The one arc re-cut to about ten files converged and
landed.
