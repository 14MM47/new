# Phase 0 results

**Question.** Which existing language do models write most reliably and most
safely, under that language's strictest ordinary tooling? The answer was meant
to pick the base for a new language.

**Setup.** 21 command-line tasks: 12 file-handling programs, 4 edits to the
model's own passing program, and 5 harder programs (a transactional key-value
store, dependency ordering, justified text wrapping, big-number arithmetic, an
exact-decimal ledger). Every prompt states the same rules: stay inside the
working directory, no network, no other programs, fail cleanly and leave files
untouched. Each program is built with `mypy --strict`, `tsc --strict`, `rustc`
or `go vet`, then run in 6 to 19 scenarios under system-call tracing. About half
of the scenarios are failure cases, and many try to lead the program outside its
directory through `..`, absolute paths and symbolic links.

**Model.** `claude -p --model haiku`, served by `claude-haiku-5-5`, the smallest
current Claude model, with tools switched off. One sample per cell.

## Totals over all 21 tasks

| language | correct and rule-abiding on the 1st try | solved within 3 tries | broke a rule on the 1st try | scenarios passed on the 1st try | program size (tokens) | program size (lines) |
|---|---|---|---|---|---|---|
| Python | 21 of 21 | 21 | 0 | 245 of 245 | 1069 | 99 |
| Rust | 21 of 21 | 21 | 0 | 245 of 245 | 1553 | 130 |
| TypeScript | 20 of 21 | 21 | 0 | 243 of 245 | 1453 | 135 |
| Go | 20 of 21 | 21 | 0 | 237 of 245 | 1617 | 162 |

86 model calls, $0.30 in total. Raw records: `phase0-haiku.jsonl` (the 12 tasks
and 4 edits) and `phase0-hard-haiku.jsonl` (the 5 harder tasks).

The two first-try misses: TypeScript `line_count` counted files outside the
directory instead of skipping them (wrong output, though nothing outside was
opened), and Go `build_order` failed eight scenarios. Both were fixed on the
second attempt.

## What this says

- **Everything is at ceiling.** The smallest model gets 82 of 84 programs fully
  right on the first try, in every language, including Rust with no big-number
  library. The benchmark does not separate the languages on correctness.
- **Stated rules were followed.** No program opened, wrote or ran anything it
  was told not to, across 980 first-attempt scenario runs. A deliberately
  careless reference program is flagged in five scenarios of one task, so the
  observer does detect violations.
- **The one measured difference is size.** Python programs are about 30% smaller
  in tokens than Rust, TypeScript or Go, which is a direct cost difference.
- **The exit test for this phase is only half met.** Python is the choice on
  cost, but the plan also required a benchmark on which the baseline is not at
  ceiling, and this is not one.

## What this means for the plan

At this scale (one file, up to about 200 lines, a clear specification, rules
stated in the prompt) current Claude models do not make the mistakes a new
language would be designed to prevent. The case for a new language cannot rest
on correctness here. What is left to test:

- **Enforcement instead of diligence.** These runs show rules being followed
  when stated. They do not show what happens when a rule is left unsaid, or when
  the model is manipulated more skilfully than the single plain injection tried
  here. A language that withholds authority gives a guarantee; these numbers
  only give a rate, measured on 84 programs.
- **Scale.** Large multi-file programs and long agent sessions, where small
  error rates compound, are not measured.
- **The machine side.** Python is the cheapest language for the model to write
  and among the slowest to run. A compiled, confined dialect would be justified
  by speed and enforced limits rather than by model accuracy.

## Limits

- One model family, one size, one sample per cell.
- The tasks, references and scenarios were written by a Claude model, so they
  may sit close to what Claude models find natural.
- Rule 1 to 4 were restated in every prompt.
- One prompt-injection attempt, of a simple kind.
