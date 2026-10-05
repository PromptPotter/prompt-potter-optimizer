# Reactome TypeQL — writing a query a database will run

One cell is one natural-language question about the Reactome pathway database. The model is given
the database schema, five worked examples and the question, and has to answer with a single TypeQL
3.x query in one fenced code block. The query is then **executed**, read-only, and its result is
compared with a known answer. The text of the query is never graded — only what it returns.

Each question is run once, so a cell scores 0 or 1.

## What the prompt is, and what it is not

The prompt being improved is the HEAD of the harness's own prompt: a few instruction lines and a
TypeQL language guide. Below it, fixed and outside the candidate's reach, the harness appends the
schema, the examples, an instruction to reply with exactly one fenced code block, and the question
with one line stating the required return shape (for example "Return a single integer." or "Return
one row per result. Name the output fields exactly: name, count.").

So the candidate cannot change the schema, the examples or the output contract. It can change what
the model knows about TypeQL and how it is told to go about writing a query.

## What separates a hit from a miss

A first attempt misses in one of two ways, and they are different repairs:

- **A visible error.** The query does not parse, names a type or role the schema does not have, or
  breaks a typing rule, and the database rejects it. TypeQL's type checker catches most wrong
  queries this way. These are failures of language knowledge: wrong syntax for a reduction, a
  relation written without its roles, a 2.x idiom in a 3.x query.
- **A silently wrong result.** The query runs and returns something that is not the expected
  answer. These are failures of reading: the wrong type picked from the schema, a subtype forgotten
  where the question needs the whole hierarchy, a count taken over the wrong thing, or the right
  rows in the wrong shape — a column too many, a field named differently from the one asked for.

The result has to match exactly in shape as well as content. A right answer returned with an extra
column, or with a field name other than the one the question states, is a miss.

## The question families

The questions span single-type lookups, multi-hop joins, aggregations, an argmax, recursive walks
of the pathway hierarchy, relations that carry their own attributes, and questions that must reach
every subtype of a type. A few are deliberately **unanswerable** against this schema: for those the
only correct reply is the literal token `UNANSWERABLE` on its own line, with no query — and
replying that to a question that does have an answer is a miss.

## What the score is

The score is accuracy after the retries. When the database rejects a query the harness feeds the
error back and the model may correct it, up to twice, and a run counts if any of those attempts
returns the expected result. First-attempt accuracy is recorded beside the score and is not what a
candidate is judged on. A silently wrong query is never retried at all — it ran, so it is final —
which makes those the misses no retry can repair.
