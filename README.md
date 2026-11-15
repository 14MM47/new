# Lathe

Lathe is an experimental programming language designed to be written by language
models rather than people. This repository holds version 0: a checker, a
reference interpreter, a primer for models, and a harness that measures whether
the design actually helps.

"Lathe" is a working name.

## Status

| Part | State |
|---|---|
| Line-by-line checker with verdicts and repair hints | working |
| Reference interpreter | working |
| Grants, effect recording and exact replay | working |
| Cards and plain-words search (`ask`) | working |
| Measurement harness against a Python baseline | working |
| Native code for x86-64 Linux | not started |
| Proofs of claims (anything beyond running examples) | not started |
| Sandbox enforcement by the operating system | not started; grants are enforced by the interpreter only |
| Concurrency, GPU, foreign libraries, windowing | not started |

Measured results are in [`harness/results/`](harness/results/).

## The idea in one page

Every feature is meant to follow from how a model writes text, or from what a
machine is, and to be kept only if measurement supports it.

- **Checked as written.** A `Session` is fed one line at a time and each line is
  fully checked on arrival: names, types, effects and faults. Inside a unit
  nothing can refer forward, so every accepted prefix is valid. Whole
  definitions may appear in any order in a file.
- **Closed vocabulary.** A name that does not exist is rejected with the nearest
  real names. `ask find "plain words"` searches operations by meaning.
- **Said twice.** A unit states what it is for (`why`), what must hold
  (`require`, `ensure`) and concrete `example`s. The checker runs the examples
  when the unit is closed, so the steps and the claim must agree.
- **One verdict per definition.** A verdict carries a stable code, the offending
  line, and a short list of repairs. Units that depend on a faulty unit are
  reported as blocked rather than producing a cascade.
- **Faults and traps.** An expected failure is a *fault*: it is part of a unit's
  type and every step that can fault must say what happens then. A bug is a
  *trap*: overflow, division by zero, a failed `check`. Traps stop the run.
- **Always terminates.** There are no unbounded loops. Repetition is a
  whole-collection operation, a self-call with a shrinking input, or `repeat`
  with a stated limit.
- **No authority by default.** A unit lists the effects it needs. A run is given
  a grant from outside the program, and the program cannot exceed it.
- **Replayable.** Every effect passes through one gate, so a run can be recorded
  and replayed exactly, even after the files it read are gone.

Two syntax variants exist so they can be measured against each other: the
default allows one operation per step; `--nested` allows ordinary nested
expressions with parentheses.

## Try it

Python 3.11 or later, no dependencies.

```bash
python3 -m lathe check examples/word_count.lathe --cards
```

```bash
python3 -m lathe run examples/word_count.lathe --grant "console write" --grant "files read ." README.md
```

```bash
python3 -m lathe ask find "count how often each item occurs"
```

```bash
python3 -m lathe primer
```

```bash
python3 -m unittest discover -s tests
```

`python3 -m lathe session` starts the line-by-line dialogue: each line you send
is answered with `ok` or a verdict, `undo` takes back the last accepted line,
and `ask scope`, `ask next`, `ask card NAME` and `ask find "..."` answer
questions.

## The harness

```bash
python3 -m harness.run --models openai:gpt-5.4-mini --out harness/results/run.jsonl
```

```bash
python3 -m harness.run report harness/results/run.jsonl
```

Each task is a pure function with a plain-words specification. The same model
is asked to solve it in each Lathe variant, given only the primer, and in Python
as a baseline. It gets up to three attempts; after a failed attempt it sees what
the toolchain reported or the first failing test. Model-written Python is
executed in a subprocess, so run the harness only where that is acceptable.

## Layout

| Path | Contents |
|---|---|
| `lathe/checker.py` | incremental checker, verdicts, definition hashing |
| `lathe/interp.py` | reference interpreter, grants, record and replay |
| `lathe/builtins.py` | the standard vocabulary |
| `lathe/store.py` | cards and search |
| `lathe/cli.py` | command line |
| `docs/primer.md` | what a model is given to learn the language |
| `harness/` | tasks, model callers, runner and results |
| `tests/` | unit tests |
