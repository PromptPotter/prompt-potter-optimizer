# Refactor arcs — sizing, landing, reviewing, stopping

A refactor arc closes rows of [`../specs/code-debt-cleanup.md`](../specs/code-debt-cleanup.md) in the
order of [`../specs/roadmap.md`](../specs/roadmap.md), whether run by a person or by agents.

## Size

- **A link is one decision**, with any number of mechanical sites and a few hundred changed lines at
  most; a bigger one is split before dispatch. A chain of links is one session, ordered by mechanism,
  root first; deletions (a second door, a shim, a dead field, a test the charter rejects) go before
  reshapes.
- **A census comes before the briefs.** Every holder of each fact a link touches is listed repo-wide;
  a row whose holders reach past the link's files moves the link's edge or waits for the chain that
  owns them. A row filed in passing names its chain or the reason it is held.
- **Scope and every decision are settled before the first build.** One that surfaces mid-arc is asked
  before the next build, never built ahead of the answer.
- **An arc owns its files.** Arcs that run together touch disjoint files, or name the one they share;
  arcs whose files overlap run as a chain, each link off the previous tip.
- **A brief names its changes; everything else equals main**: the wire, the optimizer treatment digests
  and decisions ([`offline-run.md`](offline-run.md)), the served responses, the feed subject each read
  names. A named change whose root needs a mechanism outside the owned files is cut and filed as a row,
  never built in.

## Landing

- **An arc joins its landing (`batch/<n>`, the chains of one census) on proof, not review**: the full
  gate green on its tip plus the brief's equals-main checks.
- **A row that moves a treatment digest lands with the chain that owns its file**, with decisions and
  requests equal to main, before the next paid run.
- **One independent review covers each landing before main.** One fix pass on the batch branch fixes
  every INTRODUCED finding; each PRE-EXISTING one becomes a ledger row; no second review round.

## Review

- **Read-only, and given the briefs and the landing's diff against main**, never a builder's report;
  a link that adds a mechanism gets a deep read, a pure deletion a re-grep of the holders.
- **Every changed capability gets a five-door row** (CLI, `/potter-run`, `embedded_run`, REST, webapp),
  each reached or named a carve-out.
- **It finds, ranked:** behaviour the brief did not name; transition code (a shim, a bridge, a reader
  of two shapes); incomplete excision; edits outside the owned files; stale docs and colliding names;
  a shallow module (a seam needs something that actually varies).
- **Each finding gives** file:line, the defect, a concrete failure scenario, INTRODUCED or PRE-EXISTING
  (`git show origin/main:<path>`), and for INTRODUCED the invariant it breaks and what would hold it.

## Fix passes and stopping

- **A fix names each finding's invariant and adds what holds it** — reverting the fix fails it: a test
  only where [`../../tests/CLAUDE.md`](../../tests/CLAUDE.md)'s charter admits one, else a check in the
  owning module or the code's shape.
- **One fix pass per batch, and one on the merge of arcs that conflict** (`git merge-tree` names the
  conflicts), never parallel passes that collide again at assembly.
- **A build that fails its proof twice stops the arc**; the operator cuts or re-scopes it. A mechanism
  whose every fix finds a new state is cut back, not hardened.
- **A stopped arc's tip is tagged `salvage/<id>`**, annotated with what its reviews found clean and what
  must not be ported; later arcs port from it piece by piece, and the last link to land deletes it.
