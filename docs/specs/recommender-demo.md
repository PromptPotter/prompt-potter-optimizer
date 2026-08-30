# The recommender — demo #1

> This page is the CASE — why this demo and not another. **There is no working dataset yet.**
> [`datasets/screen-taste-v0/`](../../datasets/screen-taste-v0/dataset.md) ships as a STUB — the
> title pools, the builder, the config entry — because its own origin read below its chance
> floor; the rows and the measurements are gone with it. Cutting the one the exhibit runs on is
> its own work-scope, after the release, under a new name. Every number about a cut belongs with
> that cut — restating them here is how two pages disagreed once already.

## The use case

Write down ten films or shows you like. Get back better recommendations than the
prompt you started with — and the prompt that produced them, to paste wherever you
actually work.

That is the whole demo, and the point is that it takes a minute. Every AI programmer
has typed some version of this prompt and settled for what came back. Almost nobody
has watched it get measurably better while they read the diff.

## Why this one

Four bars, and the candidates that failed them are as instructive as the winner.

**Most people do it.** Not "most developers" — most people. Recommendation is the
request an LLM gets more than almost any other.

**Platform-independent.** The winning prompt pastes into ChatGPT custom
instructions, a Gem, a Claude Project, a Copilot Studio agent, an n8n step, or your
own code. We optimize a *task*, not an integration — nobody has to connect anything
to try it.

**No private data.** A list of films you enjoyed sits well below the line
`ConsentGate` draws. This is what killed the strongest alternative: matching a
person's own writing voice scores higher on reach than anything else we considered,
and it needs exactly the personal material our own terms tell users not to paste.

**It has a right answer.** Style and persona prompts — where most people edit a
system prompt today — have no ground truth, so there is no fitness function and
nothing to optimize. Recommendation does, if you hold some out.

Rejected on the way here: `email-tagging` (a pilot set for an n8n microservice — not
a problem a visitor recognises as theirs), `swiss-invoices-eval` (publishable, but
Swiss four-letter account codes are locale-specific), personal-voice (above).

Scoring is what makes that fourth bar real — held out, objective, no judge in the loop,
so "better than before" has a precise meaning. The engine side of it ships: `list_rr`
scores where the held-out title landed in the returned list, graded rather than hit/miss,
because naming the right film first and naming it tenth are different answers. Which
titles are held out, and against which distractors, is the cut's to decide.

## Two risks

**The floor risk was real and landed the other way — it cost the first cut.** We feared a
metric that would score without reading the input; we got one whose origin sat *below* the
chance floor of its own draw, which is a metric that cannot move. Compute that floor before
paying for a single round: a demo whose metric cannot move demonstrates nothing, however good
the number looks on stage.

**MovieLens cannot ship.** GroupLens forbids commercial use without written
permission from a faculty member and does not generally permit redistribution, so
those rows cannot go inside a wheel of a paid product. Collect our own instead: the
exhibit needs few enough rows that asking real people is an afternoon, and it
carries no licence at all. Whatever the source, it is cut under a name nothing has
run before — the naming obligation and why it bites is
[`datasets/CLAUDE.md`](../../datasets/CLAUDE.md)'s.

## What the reference run is for

A new account lands on one finished campaign it did not run: the origin prompt, the
rounds, what changed each time, and the winner. Read-only, hideable, never theirs to
delete. It exists because the first screen otherwise asks a visitor to supply data
before it has shown them anything — which is what the account that bounced on
2026-08-28 was asked to do.

How far the mechanism that serves it has got is [`roadmap.md`](roadmap.md)'s A1 row, with
everything else in flight — a second status here is one more place to go stale.
