# SealQA LongSeal — answering from a haystack of conflicting documents

One cell is one long-context call. The question arrives with twelve documents that were retrieved
automatically for it, and the answer has to be found among them. There are no tools and nothing to
search: the retrieval already happened, badly, and what is being tested is what the model does with
the result.

The documents are not neutral. SealQA selects questions whose web search returns **conflicting**
results (sources that disagree, with no marker of which is right) or **unhelpful** ones (sources
that look on-topic and never address the question). Some questions have no document that settles
them at all.

## What separates a good answer from a bad one

Three failures, and they are different repairs:

- **Never locating the evidence.** The model skims, anchors on the first document that looks
  relevant, and never finds the one the question actually turns on.
- **Locating it and not using it.** The decisive document is read and then overridden — by a more
  confident-sounding source, by an aggregator repeating a rumour, or by what the model already
  believed before it read anything.
- **Using it and answering wrongly anyway.** Right document, wrong reading: the wrong entity from
  a list, the right fact at the wrong precision, a date that belongs to a different event.

Only the third is a comprehension failure. The first two are strategy failures, and they are what a
prompt can move — which is why this dataset grades the evidence work separately from the answer
rather than collapsing all three into one number.

## What the prompt cannot fix

The answer is graded strictly against a short gold: the name, the date, the number. A response that
finds the right document and then buries the answer in a paragraph of reasoning scores as a miss.
And where the documents genuinely do not settle the question, no prompt makes them — recognising
that and saying so is the correct behaviour, not a failure to try harder.
