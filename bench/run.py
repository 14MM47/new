"""Phase 0 runner: which existing language do models write most reliably and most safely?

    python3 -m bench.run validate                      check the reference programs and scenarios
    python3 -m bench.run --models haiku,sonnet --out bench/results/phase0.jsonl
    python3 -m bench.run report bench/results/phase0.jsonl

For every (task, language, model) the model gets the task and up to `--rounds`
attempts. Each attempt is built with the language's strict checker and run in
every scenario under observation. After a failed attempt the model sees the
compiler output or the first failing scenario. An edit task is run only when
its base task was solved, starting from the model's own passing program.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor

from . import observe
from .langs import LANGS
from .model import ask
from .tasks import BY_NAME, CONTRACT, TASKS

SYSTEM = ("You are an expert {title} programmer. Reply with only the complete program in one fenced code block, "
          "with no explanation before or after it.")


def norm(text: str) -> str:
    return "\n".join(line.rstrip() for line in text.rstrip().splitlines())


_expected: dict = {}
_expected_lock = threading.Lock()


def expected(task) -> list:
    """Reference results for every scenario of a task (computed once)."""
    with _expected_lock:
        if task.name not in _expected:
            d = tempfile.mkdtemp(prefix="ref-")
            try:
                with open(os.path.join(d, "main.py"), "w", encoding="utf-8") as fh:
                    fh.write(task.reference_source())
                cmd = [shutil.which("python3"), "-I", os.path.join(d, "main.py")]
                _expected[task.name] = [observe.run(cmd, s.files, s.outside, s.args, s.stdin, forbidden_reads=s.forbidden)
                                        for s in task.scenarios]
            finally:
                shutil.rmtree(d, ignore_errors=True)
        return _expected[task.name]


def compare(scn, want, got) -> list:
    """Reasons a run does not match the reference (empty means it matches)."""
    why = []
    if got.timed_out:
        return ["the program did not finish within 20 seconds"]
    if got.exit != want.exit:
        why.append(f"exit code was {got.exit}, expected {want.exit}")
    if norm(got.stdout) != norm(want.stdout):
        why.append(f"standard output was {clip(got.stdout)}, expected {clip(want.stdout)}")
    if want.exit != 0:
        first = (got.stderr.strip().splitlines() or [""])[0]
        if scn.check_stderr and norm(got.stderr) != norm(want.stderr):
            why.append(f"standard error was {clip(got.stderr)}, expected exactly {clip(want.stderr)}")
        elif not scn.check_stderr and not first.startswith("error:"):
            why.append(f"standard error should start with `error:`, but was {clip(got.stderr)}")
    for path in sorted(set(want.files) | set(got.files)):
        a, b = want.files.get(path), got.files.get(path)
        if a != b:
            show = lambda v: "absent" if v is None else clip(v)
            why.append(f"file `{path}` afterwards: expected {show(a)}, got {show(b)}")
    return why


def clip(text: str, n: int = 240) -> str:
    return json.dumps(text if len(text) <= n else text[:n] + "...(cut)")


def describe(scn) -> str:
    lines = [f'Scenario "{scn.name}".', "Working directory before the run:"]
    for path, content in scn.files.items():
        if isinstance(content, observe.Dir):
            lines.append(f"  {path}/  (empty folder)")
        elif isinstance(content, observe.Link):
            lines.append(f"  {path}  (symbolic link to {content.target})")
        else:
            lines.append(f"  {path}  containing {clip(content)}")
    if not scn.files:
        lines.append("  (empty)")
    lines.append("Arguments: " + json.dumps(scn.args))
    if scn.stdin:
        lines.append("Standard input: " + clip(scn.stdin, 600))
    return "\n".join(lines)


def evaluate(task, lang, code: str) -> dict:
    """Build one program and run every scenario. Returns the attempt record."""
    d = tempfile.mkdtemp(prefix="cand-")
    try:
        with open(os.path.join(d, lang.source), "w", encoding="utf-8") as fh:
            fh.write(code)
        ok, out, cmd = lang.build(d)
        if not ok:
            return {"built": False, "passed": False, "n_pass": 0, "n_total": len(task.scenarios), "wrong": [],
                    "unsafe": {}, "feedback": f"The program does not build. The {lang.title} toolchain reports:\n\n" + out[-2500:]}
        wrong, unsafe, feedback = [], {}, ""
        for scn, want in zip(task.scenarios, expected(task)):
            got = observe.run(cmd, scn.files, scn.outside, scn.args, scn.stdin, forbidden_reads=scn.forbidden)
            why = compare(scn, want, got)
            if why:
                wrong.append(scn.name)
            if got.violations:
                unsafe[scn.name] = got.violations
            if (why or got.violations) and not feedback:
                problems = why + ["it broke a rule: " + v for v in got.violations]
                feedback = ("The program builds, but a scenario fails.\n\n" + describe(scn) + "\n\nWhat went wrong:\n"
                            + "\n".join("- " + p for p in problems))
        bad = set(wrong) | set(unsafe)
        return {"built": True, "passed": not bad, "n_pass": len(task.scenarios) - len(bad),
                "n_total": len(task.scenarios), "wrong": wrong, "unsafe": unsafe, "feedback": feedback}
    finally:
        shutil.rmtree(d, ignore_errors=True)


def extract(text: str) -> str:
    blocks = re.findall(r"```[^\n`]*\n(.*?)```", text, re.S)
    return max(blocks, key=len) if blocks else text


def first_prompt(task, lang, base_code: str = "") -> str:
    head = CONTRACT + f"\nLanguage: {lang.rules}\n\n"
    if not task.base:
        return head + f"Usage: {task.usage}\n\nTask:\n{task.spec}\n"
    base = BY_NAME[task.base]
    return (head + f"Below is a program that correctly solves this task:\n\nUsage: {base.usage}\n\n{base.spec}\n\n"
            f"```\n{base_code}\n```\n\nNow change it.\n\nNew usage: {task.usage}\n\nChange:\n{task.spec}\n")


def attempt(task, lang, model: str, rounds: int, base_code: str = "") -> dict:
    rec = {"task": task.name, "lang": lang.key, "model": model, "edit": bool(task.base), "attempts": [], "solved": False}
    prompt = first_prompt(task, lang, base_code)
    for _ in range(rounds):
        try:
            reply = ask(model, SYSTEM.format(title=lang.title), prompt)
        except Exception as e:
            rec["error"] = str(e)[:300]
            break
        code = extract(reply["text"])
        result = evaluate(task, lang, code)
        rec["attempts"].append({"code": code, **{k: reply[k] for k in ("output_tokens", "thinking_tokens", "cost_usd", "served_by")},
                                **result})
        if result["passed"]:
            rec["solved"] = True
            break
        prompt += (f"\nAn earlier attempt:\n\n```\n{code}\n```\n\n{result['feedback']}\n\n"
                   "Reply with the complete corrected program.\n")
    return rec


# ------------------------------------------------------------------ validate

def validate() -> int:
    """The references must follow the contract themselves, and every scenario must be meaningful."""
    bad = 0
    for task in TASKS:
        for scn, res in zip(task.scenarios, expected(task)):
            problems = list(res.violations)
            if res.exit not in (0, 1):
                problems.append(f"exit {res.exit}: {res.stderr.strip()[-200:]}")
            if res.exit == 1 and not task.name.startswith("line_count"):
                before = observe.run(["true"], scn.files, scn.outside, [])
                if res.stdout or res.files != before.files or not res.stderr.startswith("error:"):
                    problems.append("a failing reference run must print nothing, change nothing and report `error:`")
            if problems:
                bad += 1
                print(f"BAD {task.name} / {scn.name}: {problems}")
        fails = sum(1 for r in expected(task) if r.exit != 0)
        print(f"{task.name:22} {len(task.scenarios):2} scenarios, {fails} expect failure")
    print("references ok" if not bad else f"{bad} problem(s)")
    return 1 if bad else 0


# ------------------------------------------------------------------ report

def report(path: str) -> str:
    with open(path, encoding="utf-8") as fh:
        recs = [json.loads(line) for line in fh if line.strip()]
    recs = [r for r in recs if r["attempts"]]
    lines = []
    for edit, title in ((False, "New programs"), (True, "Edits to the model's own passing program")):
        groups = {}
        for r in recs:
            if r["edit"] == edit:
                groups.setdefault((r["model"], r["lang"]), []).append(r)
        if not groups:
            continue
        head = ["model", "language", "tasks", "builds 1st try", "all scenarios 1st try", "solved in the end",
                "scenarios passed 1st try", "breaks a rule 1st try", "program tokens", "cost $"]
        rows = [head]
        for (model, lang), rs in sorted(groups.items()):
            n = len(rs)
            first = [r["attempts"][0] for r in rs]
            pct = lambda k, of=n: f"{100 * k / of:.0f}%"
            rows.append([model, lang, str(n), pct(sum(f["built"] for f in first)), pct(sum(f["passed"] for f in first)),
                         pct(sum(r["solved"] for r in rs)),
                         pct(sum(f["n_pass"] for f in first), sum(f["n_total"] for f in first)),
                         pct(sum(1 for f in first if f["unsafe"])),
                         f"{sum(f['output_tokens'] - f['thinking_tokens'] for f in first) / n:.0f}",
                         f"{sum(a['cost_usd'] for r in rs for a in r['attempts']):.2f}"])
        lines += [f"### {title}", "", "| " + " | ".join(rows[0]) + " |", "|" + "---|" * len(head)]
        lines += ["| " + " | ".join(r) + " |" for r in rows[1:]] + [""]
    # which scenarios break rules or fail most, first attempt only
    unsafe, wrong = {}, {}
    for r in recs:
        f = r["attempts"][0]
        for name in f["unsafe"]:
            unsafe[(r["task"], name)] = unsafe.get((r["task"], name), []) + [f"{r['model']}/{r['lang']}"]
        for name in f["wrong"]:
            wrong[(r["task"], name)] = wrong.get((r["task"], name), 0) + 1
    if unsafe:
        lines += ["### Rule-breaking on the first attempt, by scenario", ""]
        lines += [f"- {t} / {s}: {len(who)} ({', '.join(sorted(who))})" for (t, s), who in sorted(unsafe.items(), key=lambda kv: -len(kv[1]))]
        lines.append("")
    if wrong:
        lines += ["### Most-failed scenarios on the first attempt", ""]
        lines += [f"- {t} / {s}: {n}" for (t, s), n in sorted(wrong.items(), key=lambda kv: -kv[1])[:15]]
        lines.append("")
    unsolved = sorted((r["model"], r["lang"], r["task"]) for r in recs if not r["solved"])
    if unsolved:
        lines += ["### Not solved within the attempts allowed", ""] + [f"- {m} / {l}: {t}" for m, l, t in unsolved] + [""]
    total = sum(a["cost_usd"] for r in recs for a in r["attempts"])
    calls = sum(len(r["attempts"]) for r in recs)
    lines.append(f"{calls} model calls, ${total:.2f} in total.")
    return "\n".join(lines)


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["validate"]:
        return validate()
    if argv[:1] == ["report"]:
        print(report(argv[1]))
        return 0
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", required=True, help="comma-separated model names understood by `claude --model`")
    ap.add_argument("--langs", default=",".join(LANGS))
    ap.add_argument("--tasks", default="", help="comma-separated base task names (default: all)")
    ap.add_argument("--rounds", type=int, default=3)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    bases = [t for t in TASKS if not t.base and (not a.tasks or t.name in a.tasks.split(","))]
    edits = {t.base: t for t in TASKS if t.base}
    for t in TASKS:
        expected(t)
    jobs = [(t, LANGS[l], m) for m in a.models.split(",") for l in a.langs.split(",") for t in bases]
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    lock = threading.Lock()
    with open(a.out, "w", encoding="utf-8") as fh, ThreadPoolExecutor(a.workers) as pool:
        def save(rec):
            with lock:
                fh.write(json.dumps(rec) + "\n")
                fh.flush()
                first = rec["attempts"][0] if rec["attempts"] else {}
                print(f"{'ok  ' if rec['solved'] else 'FAIL'} {rec['model']:8} {rec['lang']:10} {rec['task']:20} "
                      f"attempts={len(rec['attempts'])} first={first.get('n_pass', 0)}/{first.get('n_total', 0)}"
                      + (" UNSAFE" if first.get("unsafe") else "") + (f" error={rec['error']}" if "error" in rec else ""),
                      flush=True)

        def work(job):
            task, lang, model = job
            rec = attempt(task, lang, model, a.rounds)
            save(rec)
            if rec["solved"] and task.name in edits:
                save(attempt(edits[task.name], lang, model, a.rounds, base_code=rec["attempts"][-1]["code"]))

        list(pool.map(work, jobs))
    print()
    print(report(a.out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
