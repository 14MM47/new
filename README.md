# A language for language models

An experiment in designing a programming language for LLMs to write, driven by
measurement rather than by taste.

## Where it stands

| Stage | What was tried | Outcome |
|---|---|---|
| v0, "Lathe" | A new language with its own syntax, designed from first principles | Built and measured. Models wrote it far less reliably than Python, at 3.5 times the size. Archived in [`v0/`](v0/README.md). |
| v2 plan | A strict, confined dialect of a language models already know, base to be chosen by measurement | Planned. |
| v2 phase 0 | Baselines for Python, TypeScript, Rust and Go on file-handling tasks with safety observation | Done. All four at ceiling on correctness and rule-following; Python about 30% smaller. See [`bench/results/`](bench/results/README.md). |

The phase 0 result changes the question. On small, clearly specified programs,
current models do not make the errors a new language would prevent, so the
remaining case for one rests on enforced limits, larger programs and execution
speed, none of which is measured yet.

## The phase 0 benchmark

```bash
python3 -m bench.run validate
```

```bash
python3 -m bench.run --models haiku --out bench/results/run.jsonl
```

```bash
python3 -m bench.run report bench/results/run.jsonl
```

The model is reached through `claude -p` with tools switched off. Each generated
program is built with its language's strict checker and run in throwaway
workspaces under `strace`; anything it opens, writes or starts outside the rules
is recorded as a violation. The benchmark compiles and runs model-written
programs, so use it only where that is acceptable.

Needs: Python 3.11+, `strace`, the `claude` command line, `mypy`, Node.js with
TypeScript and `@types/node` installed under `bench/.cache/ts`, `rustc`, and Go.

| Path | Contents |
|---|---|
| `bench/tasks.py` | task specifications, reference programs and scenarios |
| `bench/observe.py` | workspace setup, tracing and violation rules |
| `bench/langs.py` | strict build and run commands per language |
| `bench/model.py` | the `claude -p` caller |
| `bench/run.py` | runner, validation and report |
| `bench/results/` | raw records and the write-up |
| `v0/` | the first language, its harness and its results |
