# Measurement results

Thirty-two pure-function tasks, each posed identically in Python and in two
Lathe syntax variants. A model sees the task and, for Lathe, only the primer.
It gets up to three attempts; after a failure it is shown the toolchain's
verdicts or the first failing test. One sample per cell.

- `lathe`: one operation per step.
- `lathe-nested`: ordinary nested expressions with parentheses.
- `python`: the baseline, a language the models already know.

"Program tokens" is the visible size of the final solution, averaged over
solved tasks. "All output tokens" includes hidden reasoning and failed
attempts, averaged over all tasks.

## Run 1 (`run1.jsonl`)

| model | language | valid 1st try | correct 1st try | correct in the end | mean attempts | program tokens | all output tokens |
|---|---|---|---|---|---|---|---|
| gemini-3.8-flash | lathe | 81% | 78% | 97% | 1.28 | 305 | 7018 |
| gemini-3.8-flash | lathe-nested | 94% | 91% | 100% | 1.09 | 288 | 5686 |
| gemini-3.8-flash | python | 100% | 100% | 100% | 1.00 | 80 | 767 |
| gpt-5.4-mini | lathe | 28% | 25% | 50% | 2.31 | 189 | 812 |
| gpt-5.4-mini | lathe-nested | 41% | 31% | 69% | 2.03 | 219 | 634 |
| gpt-5.4-mini | python | 100% | 100% | 100% | 1.00 | 79 | 79 |

## Run 2 (`run2.jsonl`)

After changes prompted by run 1's failures: optional `why`, fewer reserved
words, definitions in any order, types pinned down by later steps, `when`
chains, `give fault` as a branch value, and a harness fix for picking the
program out of a reply. Python was not re-run.

| model | language | valid 1st try | correct 1st try | correct in the end | mean attempts | program tokens | all output tokens |
|---|---|---|---|---|---|---|---|
| gemini-3.8-flash | lathe | 94% | 94% | 100% | 1.09 | 286 | 6231 |
| gemini-3.8-flash | lathe-nested | 97% | 94% | 100% | 1.06 | 287 | 4870 |
| gpt-5.4-mini | lathe | 34% | 31% | 59% | 2.28 | 189 | 626 |
| gpt-5.4-mini | lathe-nested | 50% | 41% | 59% | 2.06 | 172 | 486 |

## What the numbers say

- **Lathe does not beat the baseline here.** Python is at 100% on the first try
  for both models, so on these tasks Lathe can only tie or lose on correctness,
  and it loses on cost.
- **Cost.** A Lathe solution is about 3.5 times the size of the Python one
  (roughly 290 against 80 tokens). For the reasoning model, total output
  including hidden reasoning is 6 to 9 times higher. The primer adds about
  3,100 input tokens to every request, above the 2,000 it was planned to fit in.
- **Learnability depends heavily on the model.** A reasoning model learns the
  language from the primer almost completely (94% first try, 100% within three
  attempts). A small model does not (31% to 41% first try, 59% in the end).
- **Nested expressions look better than one operation per step.** In all four
  model-and-run pairs the nested variant has higher first-try validity and
  about 20% fewer output tokens. The gap narrowed in run 2 and is within noise
  for any single pair.

## Limits of this evidence

- One sample per cell and 32 tasks: a difference of two or three tasks is noise.
  The small model's nested end-score fell from 69% to 59% between runs with no
  change that should have hurt it.
- Run 2 is measured on the same tasks whose failures guided the changes, so it
  is optimistic.
- Three further fixes were made after run 2 and are not measured.
- The tasks are pure functions. They exercise none of what Lathe adds: declared
  effects, grants, mandatory fault handling, guaranteed termination, replay.
  They measure the cost of the language, not its benefit. A benchmark with
  effectful tasks, where unsafe behaviour counts against a solution and the
  baseline is not at ceiling, is needed before the design can be judged.
- Two models, both mid-tier. No frontier model was measured.
