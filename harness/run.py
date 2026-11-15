"""Measurement harness: the same tasks, the same models, Lathe against a known language.

For every (task, language, model) the model gets the task, then up to `--rounds`
attempts. After a failed attempt it is shown what the language's own toolchain
says (verdicts or a traceback), or the first failing test. Results go to a JSONL
file; `report` turns one into a table.

    python3 -m harness.run --models openai:gpt-5.4-mini --out harness/results/run.jsonl
    python3 -m harness.run report harness/results/run.jsonl
"""
from __future__ import annotations

import argparse
import ast
import copy
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor

from lathe import types as T
from lathe.builtins import Fault, Trap
from lathe.checker import check_source
from lathe.cli import primer_text
from lathe.interp import Interp, Runtime, fmt
from lathe.lexer import lex

from .models import call
from .tasks import TASKS

LATHE_SYSTEM = ("You write programs in Lathe, a new programming language. Everything about Lathe is in the primer "
                "below; nothing from other languages carries over.\n\n")
PY_SYSTEM = "You write Python 3 programs using only the standard library."

PY_DRIVER = r"""
import ast, sys
payload = ast.literal_eval(sys.stdin.read())
ns = {}
try:
    exec(compile(payload["code"], "solution.py", "exec"), ns)
    fn = ns[payload["name"]]
except BaseException as e:
    import traceback
    print(repr({"invalid": "".join(traceback.format_exception_only(type(e), e)).strip()}))
    sys.exit(0)
out = []
for args in payload["tests"]:
    try:
        out.append(("ok", fn(*args)))
    except ValueError:
        out.append(("fault", None))
    except BaseException as e:
        out.append(("error", f"{type(e).__name__}: {e}"))
print(repr({"results": out}))
"""


def extract(text: str) -> str:
    blocks = re.findall(r"```[a-zA-Z0-9_+-]*\n(.*?)```", text, re.S)
    return max(blocks, key=len) if blocks else text


def prompt_for(task, lang: str) -> str:
    if lang.startswith("lathe"):
        fail = (" Where the task says to fail, give a fault." if task.fallible else "")
        return (f"Task: {task.spec}{fail}\n\nDefine a unit with exactly this name, inputs and result "
                f"(helper units may be defined above it):\n\n```\n{task.lathe_signature()}\n```\n\n"
                "Reply with only the complete Lathe program in one fenced code block.")
    fail = (" Where the task says to fail, raise ValueError." if task.fallible else "")
    return (f"Task: {task.spec}{fail}\n\nDefine a function with exactly this signature "
            f"(helper functions are allowed):\n\n```\n{task.python_signature()}\n```\n\n"
            "Reply with only the complete Python program in one fenced code block.")


def _lathe_type(src: str) -> tuple:
    return T.parse(lex(src), 0, set())[0]


def eval_lathe(task, code: str, nested: bool = False) -> dict:
    session = check_source(code, nested=nested)
    if session.verdicts:
        shown = "\n".join(v.render() for v in session.verdicts[:6])
        return {"valid": False, "passed": False, "feedback": "The checker reports:\n\n" + shown}
    u = session.prog.units.get(task.name)
    want = [_lathe_type(t) for _, t in task.params]
    if u is None or [t for _, t in u.params] != want or u.ret != _lathe_type(task.ret) or u.fallible != task.fallible:
        return {"valid": False, "passed": False,
                "feedback": f"The program must define this unit exactly:\n\n{task.lathe_signature()}"}
    for args, expected, expect_fault in task.tests():
        shown = " ".join(fmt(a) for a in args)
        try:
            got = Interp(session.prog, Runtime.sealed(), budget=3_000_000).call(task.name, copy.deepcopy(args))
            ok, got_s = (not expect_fault and got == expected), fmt(got)
        except Fault as f:
            ok, got_s = expect_fault, f'fault "{f.code}"'
        except Trap as t:
            where = f" at {t.where[0]} {t.where[1]}" if t.where else ""
            ok, got_s = False, f"a trap{where}: {t.message}"
        except RecursionError:
            ok, got_s = False, "a trap: calls nested too deep"
        if not ok:
            want_s = "a fault" if expect_fault else fmt(expected)
            return {"valid": True, "passed": False,
                    "feedback": f"The program checks, but a test fails: `{task.name} {shown}` gave {got_s}; expected {want_s}."}
    return {"valid": True, "passed": True, "feedback": ""}


def eval_python(task, code: str) -> dict:
    tests = task.tests()
    payload = repr({"code": code, "name": task.name, "tests": [a for a, _, _ in tests]})
    with tempfile.TemporaryDirectory() as tmp:
        try:
            proc = subprocess.run([sys.executable, "-I", "-c", PY_DRIVER], input=payload, capture_output=True,
                                  text=True, timeout=20, cwd=tmp)
        except subprocess.TimeoutExpired:
            return {"valid": True, "passed": False, "feedback": "The program did not finish within 20 seconds."}
    try:
        out = ast.literal_eval(proc.stdout.strip().splitlines()[-1])
    except Exception:
        return {"valid": False, "passed": False, "feedback": "Running the program failed:\n\n" + proc.stderr[-800:]}
    if "invalid" in out:
        return {"valid": False, "passed": False, "feedback": "Python reports:\n\n" + out["invalid"]}
    for (args, expected, expect_fault), (kind, got) in zip(tests, out["results"]):
        ok = (kind == "fault") if expect_fault else (kind == "ok" and got == expected and type(got) is type(expected))
        if not ok:
            shown = ", ".join(repr(a) for a in args)
            got_s = {"ok": repr(got), "fault": "ValueError", "error": str(got)}[kind]
            want_s = "ValueError" if expect_fault else repr(expected)
            return {"valid": True, "passed": False,
                    "feedback": f"A test fails: `{task.name}({shown})` gave {got_s}; expected {want_s}."}
    return {"valid": True, "passed": True, "feedback": ""}


def evaluate(task, lang: str, code: str) -> dict:
    if lang == "python":
        return eval_python(task, code)
    return eval_lathe(task, code, nested=(lang == "lathe-nested"))


def attempt(task, lang: str, model: str, rounds: int) -> dict:
    system = PY_SYSTEM if lang == "python" else LATHE_SYSTEM + primer_text(nested=(lang == "lathe-nested"))
    messages = [{"role": "user", "content": prompt_for(task, lang)}]
    rec = {"task": task.name, "lang": lang, "model": model, "rounds": [], "solved": False}
    for _ in range(rounds):
        try:
            reply = call(model, system, messages)
        except Exception as e:
            rec["error"] = str(e)[:300]
            break
        code = extract(reply["text"])
        result = evaluate(task, lang, code)
        rec["rounds"].append({"code": code, **{k: reply[k] for k in ("prompt_tokens", "visible_tokens", "output_tokens")},
                              **result})
        if result["passed"]:
            rec["solved"] = True
            break
        messages += [{"role": "assistant", "content": reply["text"]},
                     {"role": "user", "content": result["feedback"] + "\n\nReply with the complete corrected program in one fenced code block."}]
    return rec


def report(path: str) -> str:
    with open(path, encoding="utf-8") as fh:
        recs = [json.loads(line) for line in fh if line.strip()]
    recs = [r for r in recs if r["rounds"]]
    groups = {}
    for r in recs:
        groups.setdefault((r["model"], r["lang"]), []).append(r)
    head = ["model", "language", "tasks", "valid 1st try", "correct 1st try", "correct in the end",
            "mean attempts", "program tokens", "all output tokens"]
    rows = [head]
    for (model, lang), rs in sorted(groups.items()):
        n = len(rs)
        pct = lambda k: f"{100 * k / n:.0f}%"
        first = [r["rounds"][0] for r in rs]
        solved = [r for r in rs if r["solved"]]
        rows.append([model.split(":", 1)[1], lang, str(n), pct(sum(f["valid"] for f in first)),
                     pct(sum(f["passed"] for f in first)), pct(len(solved)),
                     f"{sum(len(r['rounds']) for r in rs) / n:.2f}",
                     f"{sum(r['rounds'][-1]['visible_tokens'] for r in solved) / max(len(solved), 1):.0f}",
                     f"{sum(x['output_tokens'] for r in rs for x in r['rounds']) / n:.0f}"])
    lines = ["| " + " | ".join(r) + " |" for r in rows]
    lines.insert(1, "|" + "---|" * len(head))
    failed = sorted({(r["model"].split(":", 1)[1], r["lang"], r["task"]) for r in recs if not r["solved"]})
    if failed:
        lines += ["", "Not solved:"] + [f"- {m} / {l}: {t}" for m, l, t in failed]
    codes = {}
    for r in recs:
        if r["lang"].startswith("lathe"):
            for rd in r["rounds"]:
                for code in re.findall(r"^! (E-[A-Z]+)", rd.get("feedback", ""), re.M)[:1]:
                    codes[code] = codes.get(code, 0) + 1
    if codes:
        lines += ["", "First Lathe verdict per failed attempt (both variants): "
                  + ", ".join(f"{c} x{n}" for c, n in sorted(codes.items(), key=lambda kv: -kv[1]))]
    return "\n".join(lines)


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["report"]:
        print(report(argv[1]))
        return 0
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", required=True, help="comma-separated, e.g. openai:gpt-5.4-mini,gemini:gemini-3.8-flash")
    ap.add_argument("--langs", default="lathe,lathe-nested,python",
                    help="lathe = one operation per step; lathe-nested = nested expressions; python = baseline")
    ap.add_argument("--rounds", type=int, default=3)
    ap.add_argument("--tasks", default="", help="comma-separated task names (default: all)")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    tasks = [t for t in TASKS if not a.tasks or t.name in a.tasks.split(",")]
    jobs = [(t, lang, m) for m in a.models.split(",") for lang in a.langs.split(",") for t in tasks]
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    sys.setrecursionlimit(400_000)
    threading.stack_size(256 * 1024 * 1024)
    lock = threading.Lock()
    with open(a.out, "w", encoding="utf-8") as fh, ThreadPoolExecutor(a.workers) as pool:
        def work(job):
            rec = attempt(job[0], job[1], job[2], a.rounds)
            with lock:
                fh.write(json.dumps(rec) + "\n")
                fh.flush()
                mark = "ok  " if rec["solved"] else "FAIL"
                print(f"{mark} {rec['model']:28} {rec['lang']:13} {rec['task']:24} attempts={len(rec['rounds'])}"
                      + (f"  error={rec['error']}" if "error" in rec else ""), flush=True)
        list(pool.map(work, jobs))
    print()
    print(report(a.out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
