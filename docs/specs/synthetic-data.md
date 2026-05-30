# Synthetic Data — one hold-out, a whole dataset (far-horizon)

> **Status:** speculative research. Not on the roadmap, not scheduled, possibly never solved. Captured because the payoff is large enough to keep in view.

**The bet, stated plainly:** *To make a synthetic dataset you need only one hold-out question.*

Today PromptPotter requires the operator to supply a dataset (a train split for scoring + a held-out test). The hardest part of onboarding a real pipeline is producing enough labelled samples. This concept asks: given **one** representative, correctly-answered example, can we *generate* a synthetic dataset that mimics the real world's properties, patterns, and structure well enough to optimize against — and have lift on the synthetic set transfer to production?

**What we'd accept.** We do not claim the synthetic set equals the real distribution. We accept it as a *hopeful proxy* — population-representative if we're lucky — and lean on the one genuine hold-out to keep us honest: the real question is the anchor; the synthetic population orbits it.

**What it would unlock.** Deleting the dataset-provision requirement entirely. An operator drops in a single example of "what good looks like" and the system manufactures the rest.

**Why it's hard (and may never be solvable).**
- Distribution coverage from a sample of one is, in general, ill-posed — a single point underdetermines the manifold.
- The real metric is synthetic-to-real transfer of *optimizer lift*, not synthetic accuracy; it's easy to fool yourself.
- Diversity vs. representativeness: a generator that's too creative drifts off-distribution; too conservative and it just memorizes the seed.

**Relationship to other work.** Distinct from the BYO-dataset ingest lane ([`m10-origin-resolution-checkin.md`](m10-origin-resolution-checkin.md)) — that assumes a file exists. This removes the file. Sits even further out than the optional AlphaEvolve code-harness ([`m12-plus-backlog.md`](m12-plus-backlog.md)).
