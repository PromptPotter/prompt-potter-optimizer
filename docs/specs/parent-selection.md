# Parent selection — one acquisition over the lineage tree

**Direction of travel: the loop runs three selection rules over one tree, and two of them are the same rule with a term missing.** Collapsing them is a subtraction, not a feature — `<surface-ledger>` should move down, and the descriptions of the loop get shorter because there is one thing to describe instead of three.

Scope is WHICH NODE the next round expands from. It is not sample selection (`hard_samples.json`'s acquisition — a different question over a different set, and the word **acquisition** in this file always means the tree one; grep before coining a third).

## The three rules today

| Decision | Rule | Code |
|---|---|---|
| Which arm survives the round | PoBB — θ posterior against paired priors | `scoring/selection.py::elimination_p_best` |
| Which arm becomes next round's `RoundParent` | greedy elitism — the round's winner, always | `scoring/selection.py::elect_round_winner` |
| Which ancestor a rebase fork re-expands from | UCB1 over backpropagated θ | `mask/backprop.py::select_rewind_round` |

Rows 2 and 3 answer the same question — *given everything measured, where should the next mutation come from?* — and answer it with different arithmetic on different occasions. Row 2 is row 3 with the exploration term forced to zero and the candidate set restricted to one round's arms.

## What is already built

**`mask/backprop.py` is MCTS, and says so in its own docstring.** It carries the tree (`NodeKey = (cycle_id, round)`), per-node `visits` / `value_sum` / `q`, backpropagation up each ancestor chain, and UCB1 selection against `UCB_EXPLORATION_C`. `NodeStats.visits` is subtree size across every fork, which is the statistic a per-cycle counter cannot produce.

Three of PUCT's four inputs therefore exist: **Q** (normalized θ), **N** (visits), **ΣN** (parent visits). Nothing needs building for them.

## The one missing term is P

PUCT is `Q + c · P · √ΣN / (1 + N)`. The term `backprop.py` lacks is **P — a prior over the moves available at a node**, and its absence is why the tree can only be consulted at fork time. UCB1 without a prior must visit an arm to learn anything about it, so it is only usable where every arm has already been measured — i.e. over ancestors, never over this round's fresh proposals.

**L1 is the policy.** It already emits `n_variants` proposals with `evidence_grounding` and `changes_description` per variant; what it does not emit is any statement of expectation. That is the whole addition: one field on `L1Variant`, and the tree becomes usable for the LIVE decision instead of only the rewind one.

**Ask for a PREDICTED LIFT, not a preference — the two are not interchangeable and only one is displayable.** A normalized preference is dimensionless and sums to one across the round's arms; drawn beside θ it would be a lie factor, and read across rounds it says nothing, because the same 0.4 means a different thing in a round of three than in a round of six. A predicted lift is **in θ's units, against the parent**, which buys three things a preference does not:

- It is **comparable to the realized value on one scale**, so the display is a subtraction rather than a second axis (`webapp/CLAUDE.md` § Display-data sources — one band per channel, no scale field).
- It yields the preference anyway: normalize the predicted lifts across the round's arms and you have `P`. The reverse does not hold.
- Its **error is directly readable** — predicted minus realized, in logits — which is what the gate below actually needs. A rank correlation is the weaker reading, available from this and not the other way round.

## What collapses

- `elect_round_winner`'s promotion rule becomes the acquisition evaluated at `c = 0` — one code path, one place to change the policy, one sentence in the docs.
- The rewind decision stops being a separate mechanism reached only through `fork_proposal`; it is the same acquisition ranging over ancestors as well as children.
- **`fork_proposal`'s contract is unchanged and must stay unchanged** — the layer decides WHETHER, arithmetic decides WHERE, and no panel enumerates ancestors (`application/optimization/CLAUDE.md` § The layer-control channel). A prior over *this round's own arms* is not an ancestor enumeration and does not reopen that.

## What this does NOT add

**No learned value model.** A surrogate predicting fitness from prompt text without executing it is a different, unproven proposal, and it is out of scope here: its error would have to be smaller than the lifts the loop is chasing, which are small enough to need paired comparison, discordant-pair bounding and Holm to resolve at all. **Measure that before proposing it** — fit a surrogate on the banked `measurements/` archive, hold out candidates, compare prediction error against the observed lift distribution. A negative result closes the axis, which is worth more than the feature.

Until then the value at a node stays what it is now: **measured θ, never estimated.** PUCT with a measured Q and a proposal prior is coherent; PUCT with a guessed Q is a different system.

## Five states the search can enter and cannot report

Each is computed-and-discarded, or one fold from data already served, and each names a failure that currently has **no reader** — the shape root `CLAUDE.md` says has produced this package's costliest bugs. They are listed here rather than filed separately because they are all readings of the same acquisition, and shipping the acquisition without them is what makes it unfalsifiable.

- **Visit concentration.** `accumulate_node_stats` computes `visits` for every node and `select_rewind_round` throws all of it away except one argmax. Unreportable today: *the whole campaign is one spine and nothing was ever re-expanded* — a tree that never branched is indistinguishable from one whose branches all lost. The reading is the visit distribution's shape, and it is AlphaZero's π: in that system the root's visit counts ARE the improved policy.
- **What the leader leads on.** The acquisition is a sum of a value term and an exploration term, and a scalar hides which one is carrying it. Unreportable: *this node is winning on curiosity, not on evidence* — the same distinction `ThetaCaveat` makes for level-vs-lift, one level up. Serve the two terms, never their sum alone.
- **Prior entropy across the round's arms.** Falls out of the predicted-lift field for free. Unreportable today except by reading the prompts: *L1 is not differentiating its own proposals* — mode collapse and near-duplicate candidates, which `.claude/skills/potter-self/` already names as symptoms an operator hunts by hand. A flat prior is that symptom as a number.
- **Calibration trend across rounds.** One round's predicted-vs-realized is the gate; its TREND is whether the policy is improving, degrading, or was never informative. Unreportable: *the proposer got worse and nothing said so.* This is the one reading that cannot be taken from a single round, so nothing short of a series answers it.
- **Ruler provenance on a backpropagated Q — and this one is a correctness finding, not a display gap.** `NodeStats.value_sum` adds θ across rounds, but θ is read on a ruler that GROWS: `domain/ruler.py`'s `unmeasured_delta` caveat states outright that a θ higher than last round's can be the scale shifting rather than the prompt improving. So a node's `q` sums readings taken on different rulers, and the deeper the subtree the more scales are mixed into one mean. Today that only prices a rewind; under a live acquisition it decides every round. **Establish whether the drift is material before the acquisition consumes `q`** — the overlap line already exists for exactly this comparison, and a negative result costs one reading.

## Where the five readings live — one new element, not a panel of them

**The prediction mark and these five sit on different axes, and the axis decides the home** (`webapp/CLAUDE.md` § Component conventions — two surfaces sharing no axis do not share a box). The mark is a fact about one round's arms and rides the candidates chart. Three of the five are facts about **lineage nodes**, two about **rounds as a series**, and neither is the candidates axis. Most of them therefore land on surfaces that already exist; **exactly one earns a new element.**

| Reading | Axis | Home | Source |
|---|---|---|---|
| Visit concentration | lineage node | `candidates/ForestCard` — the cladogram already draws these nodes | `/tree` |
| Value vs exploration term | lineage node | `candidates/ForestCard`, on the node | `/tree` |
| Ruler provenance on `q` | lineage node | `candidates/ForestCard`, as a caveat on the node | `/tree` |
| Prior entropy | one round | `candidates/FitnessRankSummary` — already the round's scalar readout | `dashboard.json` |
| **Calibration trend** | rounds as a series | **new** — nothing plots a per-round scalar about the PROPOSER | `dashboard.json::rounds[]` |

**Only the calibration trend is genuinely homeless.** `eval/TrendChart` and `candidates/FitnessChart` both plot series, but both are about candidates and their fitness; a series about whether L1's predictions are getting better is a fact about the optimizer, and hanging it on a candidate chart makes it read as a candidate's number. It is a domain widget no existing pane owns, which is the stated condition for its own folder.

Four rules bind whichever way this is built:

- **Served, never derived here.** An entropy and a trend are numbers, and `webapp/CLAUDE.md` § Scoring authority admits no exception for "it's only a summary". Both land as fields, via [`../developer/adding-a-surface.md`](../developer/adding-a-surface.md) § 3.
- **One source per data class.** Node facts come from `/tree`, round facts from `dashboard.json`, and neither is stitched from the other — the merge branch that rule exists to prevent.
- **No second cladogram.** The three node readings ride `candidates/Forest` through its `CladogramCtx`; a hand-placed geometry beside it is the failure that has already happened once.
- **`<entry-point-parity>` is unresolved and must be decided, not defaulted.** A calibration trend is a comparison across rounds, which is `evidence`'s shape — so either it reaches that verb too, or browser-only is a DECLARED inversion like sample look-ahead, stated in [`../../promptpotter/presentation/CLAUDE.md`](../../promptpotter/presentation/CLAUDE.md). Shipping it to one surface and leaving the question open is the half-built case that rule names.

## Vocabulary

Evolution terms within a round, tree terms across rounds — which is already what the code does (`mask/backprop.py` has no evolutionary word in it). Root [`CLAUDE.md`](../../CLAUDE.md) § Conventions owns the list; three names to settle when the acquisition lands:

- **`selection`** means both "which individual survives the round" (`scoring/selection.py`) and "which node to expand from". This arc merges the two functions, so the word has to resolve.
- **`prior` is taken** — PoBB's paired comparison uses it for the previously-measured arm. Don't call the predicted lift a prior.
- **`NodeStats.value_sum`** sums θ, not `composite_fitness`; `theta_sum` says so. And the docstring's *"each round is one simulation"* is wrong — a round is a measurement, nothing is rolled out.

## Composes with — rebase forfeit

[`roadmap.md`](roadmap.md) § Rebase forfeit already specifies a cost term folded into this same acquisition as `Q − λ·forfeit`, λ beside `UCB_EXPLORATION_C`. **These two must land as one acquisition function, not two patches to it.** Order does not matter; landing them independently does — each is a rewrite of the same expression, and the second would silently take the shape of the first. Whichever lands second names the other in its diff.

## What it costs

- **`L1Variant` gains a field, and the field order is load-bearing.** Three surfaces move together (`application/optimization/CLAUDE.md` § L1): the Pydantic model, `l1_generate`'s `answer_format` prose, and the regenerated `resolved_schemas`. The prior generates **after** `evidence_grounding` and the mutation slots — a preference stated before the mutation is a prediction, one stated after is a rationalization, and only the second is what PUCT wants.
- **The wire schema is prompt text** (`<simplify-the-problem>`). One numeric field per variant is cheap; a free-text justification for it is not, and is not needed — `changes_description` already carries the reasoning.
- **`injection_source_digest` voids banked origins** if the renderer prose moves with it. Expect the re-measure; count first (`ls .promptpotter/projects/*/campaigns`).

## Open

- **§0 bucket.** Not yet mapped — the pre-flight gate is unanswered and this spec does not answer it. If the acquisition is a new concept rather than an extension of the mask projection's fold, §0 needs amending in its own PR landing first.
- **Whether a self-reported prediction carries signal.** An LLM's stated expectation about its own proposals may be uninformative or anti-correlated. This is measurable before any of the above is built: bank the predicted lift on existing rounds as an **inert field** — written, served, read by nothing that decides — then compare it against realized θ lift. **Build that reading first; the acquisition change is gated on it.**

  **The reading and the operator display are the same artifact.** A predicted-lift channel drawn beside the realized bar (§ below) makes the gate legible every round, by eye, at no extra cost — and an operator watching a live round is the reading. Ship the display with the inert field, not after the acquisition.

## The display half — a forward view, not an overlay

**No toggle, and no new element.** A toggle manages redundant ink; this is never redundant. `DashboardCandidate.label` is *composed at mint*, so every arm of a round has a served row before any of them is measured — and PoBB walks arms **sequentially**, so for most of a round the prediction is the ONLY thing those rows carry. Hiding it would hide the sole content of the unmeasured half; showing it after the round is the prediction error, which is the most interesting mark on the chart. One mark, always drawn, two readings by round state — the multifunctioning element rather than a second channel behind a switch.

The channel is declared in `webapp/components/candidates/series.ts` like every other. What changes per arm is its STATE, not its presence:

| Arm state | Mark |
|---|---|
| minted, unmeasured | prediction alone |
| measuring | prediction + the bar growing under it, band widening |
| elected | prediction + bar; the gap IS the error |
| cut by PoBB, never completed | prediction, marked RESOLVED-WITHOUT-MEASUREMENT |

**The fourth row is the one that will be got wrong.** An arm cut early may never receive a bar, and a prediction left in its pending mark reads as still-loading forever. This is the same failure `DashboardCandidate.invalid` already exists to prevent — *"without it the row is byte-identical to one that got everything wrong"* — so the cut state earns its own mark rather than an absence.

**Predict in θ, and accept that it resolves at ELECTION rather than continuously.** `DashboardCandidate.theta` is null on every row until the election stamps it (the fit is round-scoped and needs two arms), so a θ prediction has nothing to converge against mid-round. Predicting in the live unit instead — composite fitness, which does accumulate per sample — would buy continuous resolution at the cost of re-opening the accuracy-vs-θ confusion `webapp/components/candidates/AbilityInfo.tsx` exists to close, by drawing the comparison in a unit the round is not won on. **One scale, one meaning.** The forward view's value is seeing the whole round's expectation laid out before anything is measured, which needs no continuous resolution.

Whether the prediction carries a band is open, and the default is NO: it has no sampling error, and a whisker drawn beside a measured one would claim it was measured (`webapp/CLAUDE.md` — two whiskers drawn alike have to mean alike).
- **Normalization across forks.** `select_rewind_round` min-max normalizes θ across the forest so `UCB_EXPLORATION_C` means what it says, and notes that a degenerate forest collapses to pure exploration. A live acquisition ranging over unmeasured children has no θ for them at all — what stands in, and whether the normalization window is the forest or the round, is undecided.
- **Interaction with PoBB.** If the parent can be a node other than this round's winner, an arm cut early by `elimination_p_best` may still be the acquisition's pick on the exploration term. Whether an eliminated arm is a legal parent is a policy question, not a derivation.
