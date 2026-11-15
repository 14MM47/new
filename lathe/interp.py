"""Reference interpreter: the definition of what a checked Lathe program means.

It also owns the runtime side of effects: a `Grant` says what a run may touch,
and every effect passes through `Runtime.effect`, which is what makes a run
recordable and exactly replayable.
"""
from __future__ import annotations

import json
import os
import random
import sys
import threading
import time
from typing import Optional

from .builtins import BUILTINS, EFFECTS, Fault, Rec, Trap, chk

MAX_DEPTH = 20_000
PATH_EFFECTS = ("files read", "files write")


def fmt(v) -> str:
    """Show a runtime value in Lathe's own notation where one exists."""
    if v is None:
        return "nothing"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, str):
        return json.dumps(v, ensure_ascii=False)
    if isinstance(v, list):
        return "[" + ", ".join(fmt(x) for x in v) + "]"
    if isinstance(v, Rec):
        return v.kind + "(" + ", ".join(f"{k} = {fmt(x)}" for k, x in v.items()) + ")"
    if isinstance(v, dict):
        return "{" + ", ".join(f"{fmt(k)}: {fmt(x)}" for k, x in v.items()) + "}"
    if isinstance(v, tuple):
        return v[0] if v[1] is None and v[0] != "some" else f"{v[0]} {fmt(v[1])}"
    return repr(v)


class Grant:
    """What a run is allowed to touch. Issued from outside the program."""

    def __init__(self):
        self.effects: dict = {}     # effect -> None (anywhere) or list of folder roots

    @classmethod
    def parse(cls, specs: list) -> "Grant":
        g = cls()
        for spec in specs:
            for part in spec.split(","):
                part = part.strip()
                if not part:
                    continue
                eff = next((e for e in sorted(EFFECTS, key=len, reverse=True)
                            if part == e or part.startswith(e + " ")), None)
                if eff is None:
                    raise ValueError(f"`{part}` does not start with an effect; effects are: {', '.join(EFFECTS)}")
                rest = part[len(eff):].strip()
                if rest and eff not in PATH_EFFECTS:
                    raise ValueError(f"`{eff}` takes no path")
                if rest:
                    roots = g.effects.setdefault(eff, [])
                    if roots is not None:
                        roots.append(os.path.realpath(rest))
                else:
                    g.effects[eff] = None
        return g

    def path_ok(self, effect: str, path: str) -> bool:
        if effect not in self.effects:
            return False
        roots = self.effects[effect]
        if roots is None:
            return True
        real = os.path.realpath(path)
        return any(real == r or real.startswith(r.rstrip(os.sep) + os.sep) for r in roots)

    def describe(self) -> str:
        parts = []
        for eff in sorted(self.effects):
            roots = self.effects[eff]
            parts.append(eff if roots is None else eff + " " + " ".join(roots))
        return ", ".join(parts) or "(nothing)"


class Runtime:
    def __init__(self, grant: Grant, stdin=None, stdout=None, record: Optional[list] = None,
                 replay: Optional[list] = None):
        self.grant = grant
        self.stdin = stdin if stdin is not None else sys.stdin
        self.stdout = stdout if stdout is not None else sys.stdout
        self.record = record
        self.replay = list(replay) if replay is not None else None

    @classmethod
    def sealed(cls) -> "Runtime":
        return cls(Grant())

    def effect(self, op: str, args: list, thunk):
        """Every effect goes through here: checked against the grant, recorded, replayable."""
        b = BUILTINS[op]
        if b.effect not in self.grant.effects:
            raise Trap("grant", f"`{op}` needs `{b.effect}`, which this run was not granted")
        if self.replay is not None:
            if not self.replay:
                raise Trap("replay", f"the recording has no more events but the program asked for `{op}`")
            ev = self.replay.pop(0)
            if ev["op"] != op or ev["args"] != args:
                raise Trap("replay", f"the recording expected `{ev['op']}` but the program asked for `{op}`")
            if op == "console.write":
                self.console_write(args[0])
            if "fault" in ev:
                raise Fault(ev["fault"])
            return ev["value"]
        try:
            value = thunk()
        except Fault as f:
            if self.record is not None:
                self.record.append({"op": op, "args": args, "fault": f.code})
            raise
        if self.record is not None:
            self.record.append({"op": op, "args": args, "value": value})
        return value

    # --- the actual effects ---

    def console_write(self, text: str):
        self.stdout.write(text + "\n")

    def console_read_line(self) -> str:
        line = self.stdin.readline()
        if line == "":
            raise Fault("end")
        return line.rstrip("\n")

    def _guard(self, effect: str, path: str):
        if not self.grant.path_ok(effect, path):
            raise Fault("denied")

    @staticmethod
    def _os(fn):
        try:
            return fn()
        except FileNotFoundError:
            raise Fault("missing")
        except PermissionError:
            raise Fault("denied")
        except (OSError, UnicodeError):
            raise Fault("io")

    def file_read(self, path: str) -> str:
        self._guard("files read", path)

        def go():
            with open(path, encoding="utf-8") as fh:
                return fh.read()
        return self._os(go)

    def file_write(self, path: str, text: str):
        self._guard("files write", path)

        def go():
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(text)
        return self._os(go)

    def file_exists(self, path: str) -> bool:
        return self.grant.path_ok("files read", path) and os.path.exists(path)

    def file_list(self, path: str) -> list:
        self._guard("files read", path)
        return self._os(lambda: sorted(os.listdir(path)))

    def clock_now_ms(self) -> int:
        return int(time.time() * 1000)

    def random_int(self, lo: int, hi: int) -> int:
        return random.randint(lo, hi)


class Interp:
    def __init__(self, prog, rt: Runtime, budget: Optional[int] = None):
        self.prog, self.rt, self.budget = prog, rt, budget
        self.ticks = 0
        self.depth = 0
        self.measures: dict = {}

    # ------------------------------------------------------------ values

    def atom(self, a: tuple, env: dict):
        k = a[0]
        if k == "lit":
            return a[1]
        if k == "name":
            return env[a[1]]
        if k == "field":
            return self.atom(a[1], env)[a[2]]
        if k == "list":
            return [self.atom(x, env) for x in a[1]]
        if k == "const":
            return BUILTINS[a[1]].impl(self.rt, [], None)
        if k == "case":
            return (a[2], None)
        if k == "result":
            return env["result"]
        if k == "expr":
            return self.expr(a[1], env)
        raise AssertionError(a)

    # ------------------------------------------------------------ units

    def apply(self, name: str, args: list):
        """Call whatever follows `with`: a unit or a built-in operation."""
        if name in self.prog.units:
            return self.call(name, args)
        return BUILTINS[name].impl(self.rt, args, None)

    def call(self, name: str, args: list):
        u = self.prog.units[name]
        self.depth += 1
        if self.depth > MAX_DEPTH:
            self.depth -= 1
            raise Trap("depth", f"calls are nested more than {MAX_DEPTH} deep")
        env = {pn: v for (pn, _), v in zip(u.params, args)}
        stack = None
        if u.shrinks:
            head, *fields = u.shrinks.split(".")
            m = env[head]
            for f in fields:
                m = m[f]
            m = m if isinstance(m, int) else len(m)
            stack = self.measures.setdefault(name, [])
            if stack and not (0 <= m < stack[-1]):
                self.depth -= 1
                t = Trap("measure", f"`{name}` called itself but `{u.shrinks}` did not get smaller "
                                    f"(was {stack[-1]}, now {m})")
                t.where = (name, "shrinks")
                raise t
            stack.append(m)
        try:
            for cond, src in u.requires:
                if not self.expr(cond, env):
                    t = Trap("require", f"`{name}` requires {src}")
                    t.where = (name, "require")
                    raise t
            for st in u.steps:
                self.ticks += 1
                if self.budget is not None and self.ticks > self.budget:
                    raise Trap("budget", "the run took more steps than its budget allows")
                try:
                    done, value = self.step(u, st, env)
                except Trap as t:
                    if t.where is None:
                        t.where = (name, st.label)
                    raise
                if done:
                    return value
            raise AssertionError("checked unit fell off its end")
        except Fault as f:
            f.given = False         # outside this unit it is an ordinary fault again
            raise
        finally:
            self.depth -= 1
            if stack is not None:
                stack.pop()

    def step(self, u, st, env: dict):
        k = st.kind
        if k == "bind":
            env[st.name] = self.guarded(st.expr, env, st.handler)
        elif k == "do":
            self.guarded(st.expr, env, st.handler)
        elif k == "check":
            if not self.expr(st.expr, env):
                raise Trap("check", f"check failed: {st.code}")
        elif k == "give_fault":
            if st.cond is None or self.expr(st.cond, env):
                raise Fault(st.code)
        elif k == "give":
            if st.cond is None or self.expr(st.cond, env):
                value = self.expr(st.expr, env)
                for cond, src in u.ensures:
                    if not self.expr(cond, {**env, "result": value}):
                        raise Trap("ensure", f"`{u.name}` must ensure {src}; it gave {fmt(value)}")
                return True, value
        return False, None

    def guarded(self, e: tuple, env: dict, handler: Optional[tuple]):
        try:
            return self.expr(e, env)
        except Fault as f:
            if handler is None or f.given:
                raise
            if handler[0] == "give_fault":
                if handler[1] is not None:
                    raise Fault(handler[1])
                raise
            return self.atom(handler[1], env)

    # ------------------------------------------------------------ expressions

    def expr(self, e: tuple, env: dict):
        k = e[0]
        if k == "atom":
            return self.atom(e[1], env)
        if k == "binop":
            if e[1] in ("and", "or"):       # the right side is only looked at when it matters
                left = self.atom(e[2], env)
                return left if left == (e[1] == "or") else self.atom(e[3], env)
            return self.binop(e[1], self.atom(e[2], env), self.atom(e[3], env))
        if k == "not":
            return not self.atom(e[1], env)
        if k == "raise":
            raise Fault(e[1], given=True)
        if k == "builtin":
            b = BUILTINS[e[1]]
            args = [self.atom(a, env) for a in e[2]]
            fn = None
            if e[3]:
                extras = [self.atom(a, env) for a in e[4]]
                unit = e[3]
                fn = lambda *handed: self.apply(unit, list(handed) + extras)
            return b.impl(self.rt, args, fn)
        if k == "call":
            return self.call(e[1], [self.atom(a, env) for a in e[2]])
        if k == "new":
            return Rec(e[1], {f: self.atom(a, env) for f, a in e[2]})
        if k == "case":
            return (e[2], self.atom(e[3], env) if e[3] is not None else None)
        if k == "when":
            return self.expr(e[2] if self.expr(e[1], env) else e[3], env)
        if k == "match":
            case, payload = self.atom(e[1], env)
            for name, binder, body, handler in e[2]:
                if name == case:
                    inner = {**env, binder: payload} if binder else env
                    return self.guarded(body, inner, handler)
            raise AssertionError("checked match missed a case")
        if k == "repeat":
            state = self.atom(e[1], env)
            extras = [self.atom(a, env) for a in e[3]]
            for _ in range(max(self.atom(e[4], env), 0)):
                kind, value = self.apply(e[2], [state] + extras)
                if kind == "done":
                    return value
                state = value
            raise Fault("limit")
        raise AssertionError(e)

    @staticmethod
    def binop(op: str, a, b):
        if op == "+":
            return chk(a + b)
        if op == "-":
            return chk(a - b)
        if op == "*":
            return chk(a * b)
        if op in ("/", "%"):
            if b == 0:
                raise Trap("divide", "division by zero")
            return chk(a // b) if op == "/" else a % b
        if op == "==":
            return a == b
        if op == "!=":
            return a != b
        if op == "<":
            return a < b
        if op == "<=":
            return a <= b
        if op == ">":
            return a > b
        if op == ">=":
            return a >= b
        if op == "and":
            return a and b
        if op == "or":
            return a or b
        raise AssertionError(op)


def big_stack(fn, *args, **kwargs):
    """Run fn on a thread with a large stack so deep Lathe recursion traps cleanly instead of crashing."""
    box = {}

    def target():
        try:
            box["value"] = fn(*args, **kwargs)
        except BaseException as e:   # re-raised on the calling thread
            box["error"] = e

    old_limit = sys.getrecursionlimit()
    sys.setrecursionlimit(max(old_limit, MAX_DEPTH * 12))
    threading.stack_size(512 * 1024 * 1024)
    try:
        th = threading.Thread(target=target)
        th.start()
        th.join()
    finally:
        threading.stack_size(0)
        sys.setrecursionlimit(old_limit)
    if "error" in box:
        raise box["error"]
    return box.get("value")
