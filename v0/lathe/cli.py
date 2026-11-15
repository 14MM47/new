"""Command line: check, run, session, ask, primer."""
from __future__ import annotations

import argparse
import json
import os
import sys

from . import store
from .builtins import Fault, Trap
from .checker import Session, check_source
from .interp import Grant, Interp, Runtime, big_stack, fmt
from .lexer import lex


NESTED = False      # set from --nested: the syntax variant with nested expressions


def _load(path: str) -> Session:
    with open(path, encoding="utf-8") as fh:
        return check_source(fh.read(), nested=NESTED)


def cmd_check(a) -> int:
    s = _load(a.file)
    if a.json:
        print(json.dumps({"ok": not s.verdicts, "verdicts": [v.as_dict() for v in s.verdicts],
                          "units": {n: u.hash for n, u in s.prog.units.items()}}, indent=1))
        return 1 if s.verdicts else 0
    for v in s.verdicts:
        print(v.render())
    if s.verdicts:
        print(f"{len(s.verdicts)} fault(s)")
        return 1
    p = s.prog
    print(f"ok: {len(p.units)} unit(s), {len(p.records)} record(s), {len(p.choices)} choice(s)")
    if a.cards:
        for group, show in ((p.records, store.record_card), (p.choices, store.choice_card), (p.units, store.unit_card)):
            for d in group.values():
                print(show(d))
    return 0


def run_program(prog, grant: Grant, args: list, record=None, replay=None, stdin=None, stdout=None) -> int:
    """Run `main`. Returns the process exit code."""
    err = sys.stderr
    if "main" not in prog.units:
        print("! E-MAIN: there is no `unit main` to run", file=err)
        return 2
    main = prog.units["main"]
    takes_args = [t for _, t in main.params] == [("seq", ("text",))]
    if main.params and not takes_args:
        print("! E-MAIN: `main` takes nothing, or exactly `takes args : seq of text`", file=err)
        return 2
    missing = sorted(main.needs - set(grant.effects))
    if missing:
        print(f"! E-GRANT: `main` needs `{', '.join(missing)}` but the grant is: {grant.describe()}", file=err)
        print(f"  fix: run with --grant \"{', '.join(sorted(main.needs))}\"", file=err)
        return 2
    rt = Runtime(grant, stdin=stdin, stdout=stdout, record=record, replay=replay)
    try:
        Interp(prog, rt).call("main", [args] if takes_args else [])
    except Fault as f:
        print(f'! FAULT: main gave fault "{f.code}"', file=err)
        return 1
    except Trap as t:
        where = f" at {t.where[0]} {t.where[1]}" if t.where else ""
        print(f"! TRAP {t.kind}{where}: {t.message}", file=err)
        return 3
    return 0


def cmd_run(a) -> int:
    s = _load(a.file)
    if s.verdicts:
        for v in s.verdicts:
            print(v.render(), file=sys.stderr)
        return 2
    try:
        grant = Grant.parse(a.grant or [])
    except ValueError as e:
        print(f"! E-GRANT: {e}", file=sys.stderr)
        return 2
    replay = None
    if a.replay:
        with open(a.replay, encoding="utf-8") as fh:
            replay = [json.loads(line) for line in fh if line.strip()]
    record = [] if a.record else None
    try:
        return run_program(s.prog, grant, a.args, record=record, replay=replay)
    finally:
        if a.record:
            with open(a.record, "w", encoding="utf-8") as fh:
                for ev in record:
                    fh.write(json.dumps(ev) + "\n")


def answer(session: Session, question: str) -> str:
    """Answer an `ask ...` line."""
    try:
        toks = lex(question)
    except Exception as e:
        return f"! E-SYNTAX: {e}"
    words = [str(v) for _, v in toks]
    if words[:1] == ["ask"]:
        words = words[1:]
    if not words:
        return "ask what? forms: `ask find \"plain words\"`, `ask card NAME`, `ask scope`, `ask next`"
    verb, rest = words[0], words[1:]
    if verb == "find" and rest:
        hits = store.find(session.prog, " ".join(rest))
        return "\n".join(line for _, line in hits) or "nothing matches; try other words"
    if verb == "card" and rest:
        c = store.card(session.prog, rest[0])
        return c or f"`{rest[0]}` is not a unit, record, choice or operation"
    if verb == "scope":
        u = session.cur
        if u is None or not hasattr(u, "scope"):
            return "not inside a unit; defined so far: " + (", ".join(
                list(session.prog.records) + list(session.prog.choices) + list(session.prog.units)) or "(nothing)")
        from . import types as T
        return "\n".join(f"{n} : {T.show(t)}" for n, t in u.scope.items()) or "(no names yet)"
    if verb == "next":
        return "\n".join(session.expect())
    return "forms: `ask find \"plain words\"`, `ask card NAME`, `ask scope`, `ask next`"


def cmd_session(a) -> int:
    """Line-by-line dialogue: each line gets `ok`, a verdict, or an answer."""
    s = Session(interactive=True, nested=NESTED)
    if a.file:
        with open(a.file, encoding="utf-8") as fh:
            for line in fh:
                s.feed(line.rstrip("\n"))
    for line in sys.stdin:
        line = line.rstrip("\n")
        word = line.strip().split(" ", 1)[0]
        if word == "ask":
            print(answer(s, line))
        elif word == "undo":
            print("ok" if s.undo() else "nothing to undo")
        elif not line.strip():
            continue
        else:
            verdicts = s.feed(line)
            print("\n".join(v.render() for v in verdicts) if verdicts else "ok")
        sys.stdout.flush()
    return 0


def cmd_ask(a) -> int:
    s = _load(a.file) if a.file else Session()
    print(answer(s, " ".join(a.question)))
    return 0


def primer_text(nested: bool = False) -> str:
    """The hand-written primer for one syntax variant, plus the vocabulary generated from the built-in table."""
    import re
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "docs", "primer.md")
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    keep, drop = ("nested", "flat") if nested else ("flat", "nested")
    text = re.sub(rf"<!-- {drop} -->.*?<!-- /{drop} -->\n?", "", text, flags=re.S)
    text = re.sub(rf"<!-- /?{keep} -->\n?", "", text)
    text = text.rstrip() + "\n\n## Vocabulary\n\n"
    text += ("Every built-in operation. `A B K V S R` stand for any type. `UNIT[X -> Y]` after `with` is a unit\n"
             "or operation that is handed X (plus any extra inputs you list) and gives Y.\n\n```\n")
    from .builtins import BUILTINS
    for b in BUILTINS.values():
        text += b.signature() + (f"   needs {b.effect}" if b.effect else "") + "\n"
    return text + "```\n"


def cmd_primer(a) -> int:
    sys.stdout.write(primer_text(a.nested))
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="lathe", description="Lathe: checker and reference interpreter")
    ap.add_argument("--nested", action="store_true", help="syntax variant: allow parenthesised, nested expressions")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("check", help="check a program and print verdicts")
    p.add_argument("file")
    p.add_argument("--json", action="store_true")
    p.add_argument("--cards", action="store_true", help="print the card of every definition")
    p.set_defaults(fn=cmd_check)

    p = sub.add_parser("run", help="check, then run `main` under a grant")
    p.add_argument("file")
    p.add_argument("--grant", action="append", help='e.g. --grant "console write" --grant "files read ./data"')
    p.add_argument("--record", help="write every effect to this trace file")
    p.add_argument("--replay", help="take every effect result from this trace file")
    p.add_argument("args", nargs="*")
    p.set_defaults(fn=cmd_run)

    p = sub.add_parser("session", help="line-by-line dialogue on stdin/stdout")
    p.add_argument("file", nargs="?", help="definitions to load first")
    p.set_defaults(fn=cmd_session)

    p = sub.add_parser("ask", help='ask a question, e.g. lathe ask find "count words"')
    p.add_argument("--file", help="program whose definitions are also searched")
    p.add_argument("question", nargs="+")
    p.set_defaults(fn=cmd_ask)

    p = sub.add_parser("primer", help="print the language primer")
    p.set_defaults(fn=cmd_primer)

    a = ap.parse_args(argv)
    global NESTED
    NESTED = a.nested
    return big_stack(a.fn, a)


if __name__ == "__main__":
    sys.exit(main())
