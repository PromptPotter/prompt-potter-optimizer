# Screen Taste (v0) — Order a Slate by Someone's Taste

Read ten films or series one person likes, then put ten candidates in the order they would like
them. This is the demo case: the input is a list a person can type from memory, and the output is
an ordering they can judge at a glance.

## Domain

- Input: ten titles they like, then ten candidates — everything the model knows about this person
- Output: all ten candidates, one per line, best first, each copied exactly as given
- Films and series are one pool, not two. The person keeps one list, and the strongest signal in it
  is often a pair that crosses the line — a series and the film that closes it.

## Success criteria

- Exactly one candidate is a title this person actually likes, held out of the input. The score is
  where it lands in the returned order — first is 1.0, second 0.5, tenth 0.1.
- The slate arrives shuffled, so leaving it in the order given is the same as guessing. Every rank
  the right title climbs is the only thing that pays.

## Key failure modes (the hard part)

- **Ordering by fame.** The candidates are widely-known titles and the held-out one usually is not
  the most famous among them, so "put the biggest film first" is a fluent answer that loses.
- **Reading one title instead of ten.** A single striking entry in the input pulls the ordering
  toward its genre; what the ten have in common is the actual signal.
- **Ordering by one axis.** Genre alone, era alone or medium alone each split the slate wrongly on
  their own — the liked list crosses all three deliberately.
- Dropping, adding or rewording a candidate. The score reads the line as returned, so a paraphrase
  scores as a miss even when the judgment behind it was right.
- Wrapping the list in prose. Every line counts as an item, so a sentence above the list pushes
  every real answer down one rank.

## Notes

- Small demo set (20 rows), one person's own list. Each row is a different draw from it, so the same
  taste is asked twenty different ways and no single row decides the round.
- The starting prompt is a floor: it states the task and the output shape and gives no rule about
  what this person likes, which is exactly what the optimizer has room to discover.
