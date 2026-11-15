"""Incremental checker.

A `Session` is fed one line at a time. Every line is fully checked when it
arrives (names, types, effects, faults), so any accepted prefix of a program
is known to be valid. Nothing may refer forward.

Batch mode (interactive=False): the first fault in a definition is reported and
the rest of that definition is skipped, so one definition yields one verdict.
Interactive mode: a faulty line is rejected and the state is left untouched,
so the author can send a corrected line.
"""
from __future__ import annotations

import copy
import difflib
import hashlib
from dataclasses import dataclass, field
from typing import Optional

from . import types as T
from .builtins import BUILTINS, BUILTIN_CHOICES, EFFECTS, NAMESPACES, Fault, Trap
from .lexer import BINOPS, COMPARES, KEYWORDS, LexError, lex

HEADER_ORDER = ["why", "takes", "gives", "needs", "shrinks", "require", "ensure", "example"]
KEY_TYPES = (T.INT, T.TEXT, T.BOOL)
ORDERED_TYPES = (T.INT, T.TEXT)


class Err(Exception):
    def __init__(self, code: str, message: str, fixes: Optional[list] = None):
        super().__init__(message)
        self.code, self.message, self.fixes = code, message, fixes or []


@dataclass
class Verdict:
    code: str
    message: str
    fixes: list
    line: int
    definition: Optional[str] = None
    label: Optional[str] = None
    source: str = ""

    def render(self) -> str:
        where = " ".join(x for x in (self.definition, self.label) if x)
        head = f"! {self.code} line {self.line}" + (f" in {where}" if where else "") + f": {self.message}"
        at = [f"  at: {self.source}"] if self.source else []
        return "\n".join([head] + at + [f"  fix: {f}" for f in self.fixes])

    def as_dict(self) -> dict:
        return {"code": self.code, "line": self.line, "definition": self.definition, "label": self.label,
                "source": self.source, "message": self.message, "fixes": self.fixes}


@dataclass
class Record:
    name: str
    fields: list = field(default_factory=list)      # [(name, type)]
    hash: str = ""
    lines: list = field(default_factory=list)


@dataclass
class Choice:
    name: str
    cases: list = field(default_factory=list)       # [(name, type or None)]
    hash: str = ""
    lines: list = field(default_factory=list)


@dataclass
class Step:
    label: str
    kind: str                    # bind | give | give_fault | check | do
    line: int
    name: Optional[str] = None
    expr: Optional[tuple] = None
    type: Optional[tuple] = None
    handler: Optional[tuple] = None   # ("give_fault",) | ("default", atom)
    cond: Optional[tuple] = None
    code: Optional[str] = None


@dataclass
class Unit:
    name: str
    why: Optional[str] = None
    params: list = field(default_factory=list)      # [(name, type)]
    ret: Optional[tuple] = None
    fallible: bool = False
    needs: set = field(default_factory=set)
    shrinks: Optional[str] = None
    requires: list = field(default_factory=list)    # [(expr, source text)]
    ensures: list = field(default_factory=list)
    examples: list = field(default_factory=list)    # [(arg atoms, expected atom or ("fault", code), line)]
    steps: list = field(default_factory=list)
    deps: set = field(default_factory=set)
    hash: str = ""
    lines: list = field(default_factory=list)
    evidence: str = "assumed"
    # checking state, not part of the finished unit's meaning
    scope: dict = field(default_factory=dict)
    used: set = field(default_factory=set)
    stage: int = 0
    done: bool = False
    calls_self: bool = False
    pending: Optional[dict] = None
    subst: dict = field(default_factory=dict)     # types still being worked out across steps


class Program:
    def __init__(self):
        self.records: dict = {}
        self.choices: dict = {}
        self.units: dict = {}
        self.poisoned: set = set()

    def named_types(self) -> set:
        return set(self.records) | set(self.choices)

    def taken(self, name: str) -> Optional[str]:
        if name in self.records:
            return "record"
        if name in self.choices:
            return "choice"
        if name in self.units:
            return "unit"
        return None


class Cur:
    """Token cursor over one line."""

    def __init__(self, toks: list):
        self.t, self.i = toks, 0

    def peek(self, ahead: int = 0) -> tuple:
        j = self.i + ahead
        return self.t[j] if j < len(self.t) else (None, None)

    def next(self) -> tuple:
        tok = self.peek()
        self.i += 1
        return tok

    def at_end(self) -> bool:
        return self.i >= len(self.t)

    def is_word(self, word: str) -> bool:
        k, v = self.peek()
        return k in ("name", "op") and v == word

    def accept(self, word: str) -> bool:
        if self.is_word(word):
            self.i += 1
            return True
        return False

    def expect(self, word: str, context: str):
        if not self.accept(word):
            got = self.peek()[1]
            found = f"`{_tok(self.peek())}`" if got is not None else "the end of the line"
            raise Err("E-SYNTAX", f"expected `{word}` {context}, found {found}")

    def end(self, context: str):
        if not self.at_end():
            raise Err("E-SYNTAX", f"unexpected `{_tok(self.peek())}` {context}")


def _tok(tok: tuple) -> str:
    kind, val = tok
    return '"' + val.replace('"', '\\"') + '"' if kind == "string" else str(val)


def _near(word: str, candidates) -> list:
    return difflib.get_close_matches(word, list(candidates), n=3, cutoff=0.6)


def _plain_name(tok: tuple, what: str) -> str:
    kind, val = tok
    if kind == "name" and val in KEYWORDS:
        raise Err("E-SYNTAX", f"`{val}` is a reserved word and cannot be used as a name",
                  [f"choose another name, for example `{val}_value`"])
    if kind != "name" or "." in val:
        shown = f"`{_tok(tok)}`" if val is not None else "the end of the line"
        raise Err("E-SYNTAX", f"expected {what}, found {shown}",
                  ["names are lower-case words such as `total` or `word_count`"])
    return val


class Session:
    def __init__(self, interactive: bool = False, run_examples: bool = True, nested: bool = False):
        self.prog = Program()
        self.nested = nested            # syntax variant: allow parenthesised, nested expressions
        self._fall = False              # did the line being checked contain something that can fault
        self._allow_result = False
        self.interactive = interactive
        self.run_examples = run_examples
        self.cur = None            # definition being written: Record | Choice | Unit
        self.skipping = False
        self.lineno = 0
        self.verdicts: list = []
        self.history: list = []    # interactive undo stack

    # ------------------------------------------------------------ driving

    def feed(self, line: str, lineno: Optional[int] = None) -> list:
        """Check one line. Returns the verdicts it produced (empty means accepted)."""
        self.lineno = lineno if lineno is not None else self.lineno + 1
        self._fall = self._allow_result = False
        label = snapshot = None
        try:
            toks = lex(line)
            if not toks:
                return []
            if len(toks) > 2 and toks[1] == ("op", ":") and toks[0][0] == "name" and isinstance(self.cur, Unit):
                label = toks[0][1]
            snapshot = copy.deepcopy(self.cur) if self.interactive else None
            self._line(toks, line)
            if self.interactive:
                self.history.append(snapshot)
            return []
        except LexError as e:
            err = Err("E-SYNTAX", str(e))
        except T.TypeError_ as e:
            err = Err("E-TYPE", str(e))
        except Err as e:
            err = e
        if self.interactive:
            self.cur = snapshot
        return [self._fault(err, label, line.strip())]

    def _fault(self, err: Err, label: Optional[str], source: str = "") -> Verdict:
        name = self.cur.name if self.cur is not None else None
        v = Verdict(err.code, err.message, err.fixes, self.lineno, name, label, source)
        self.verdicts.append(v)
        if not self.interactive:
            if self.cur is not None:
                self.prog.poisoned.add(self.cur.name)
            self.skipping = name or True
            self.cur = None
        return v

    def finish(self) -> list:
        """Call at end of input."""
        if self.cur is not None:
            name = self.cur.name
            v = Verdict("E-END", f"`{name}` was never closed", [f"add the line `end {name}`"], self.lineno, name)
            self.verdicts.append(v)
            self.prog.poisoned.add(name)
            self.cur = None
            return [v]
        return []

    def undo(self) -> bool:
        if not self.history:
            return False
        self.cur = self.history.pop()
        return True

    def expect(self) -> list:
        """What may come next, as line openers."""
        c = self.cur
        if c is None:
            return ["record NAME", "choice NAME", "unit NAME"]
        if isinstance(c, Record):
            return ["FIELD : TYPE"] + ([f"end {c.name}"] if c.fields else [])
        if isinstance(c, Choice):
            return ["CASE", "CASE : TYPE"] + ([f"end {c.name}"] if c.cases else [])
        if c.pending:
            case, payload = c.pending["cases"][len(c.pending["arms"])]
            return [f"on {case} NAME then ..." if payload is not None else f"on {case} then ..."]
        if c.done:
            return [f"end {c.name}"]
        out = []
        if not c.steps:
            if c.ret is None:
                return ["why \"...\"", "takes NAME : TYPE", "gives TYPE", "gives TYPE or fault"]
            out = [h + " ..." for h in HEADER_ORDER[3:]]
        return out + ["LABEL: NAME = ...", "LABEL: give ...", "LABEL: check ...", "LABEL: do ..."]

    # ------------------------------------------------------------ dispatch

    def _line(self, toks: list, raw: str):
        first = toks[0][1] if toks[0][0] == "name" else None
        if self.skipping:
            # skip the rest of a faulty definition: up to its own `end`, or the next definition
            if first in ("record", "choice", "unit") and len(toks) == 2:
                self.skipping = False
            else:
                if first == "end" and len(toks) == 2 and toks[1][1] == self.skipping:
                    self.skipping = False
                return
        if self.cur is None:
            return self._top(toks)
        if first in ("record", "choice", "unit") and len(toks) == 2:
            name = self.cur.name
            raise Err("E-END", f"`{name}` must be closed before another definition starts",
                      [f"add the line `end {name}` before this line"])
        self.cur.lines.append(toks)
        if isinstance(self.cur, Record):
            return self._record_line(toks)
        if isinstance(self.cur, Choice):
            return self._choice_line(toks)
        return self._unit_line(toks)

    def _top(self, toks: list):
        c = Cur(toks)
        kind, word = c.next()
        if word not in ("record", "choice", "unit"):
            raise Err("E-SYNTAX", f"expected a definition, found `{_tok((kind, word))}`",
                      ["start with `record NAME`, `choice NAME` or `unit NAME`"])
        name = _plain_name(c.next(), f"a name after `{word}`")
        c.end(f"after `{word} {name}`")
        if self.prog.taken(name) or name in self.prog.poisoned:
            what = self.prog.taken(name) or "definition with a fault"
            raise Err("E-DUP", f"`{name}` is already a {what}", ["choose a different name"])
        self.cur = {"record": Record, "choice": Choice, "unit": Unit}[word](name)
        self.cur.lines.append(toks)

    def _is_end(self, toks: list) -> bool:
        if toks[0] != ("name", "end"):
            return False
        name = self.cur.name
        if len(toks) != 2 or toks[1] != ("name", name):
            raise Err("E-END", f"this definition is closed with `end {name}`", [f"write `end {name}`"])
        return True

    # ------------------------------------------------------------ record / choice

    def _record_line(self, toks: list):
        r = self.cur
        if self._is_end(toks):
            if not r.fields:
                raise Err("E-SYNTAX", f"record `{r.name}` has no fields", ["add a line `FIELD : TYPE`"])
            r.hash = self._hash(r)
            self.prog.records[r.name] = r
            self.cur = None
            return
        c = Cur(toks)
        fname = _plain_name(c.next(), "a field name")
        c.expect(":", f"after field `{fname}`")
        ftype, c.i = T.parse(toks, c.i, self._types_in_scope())
        c.end("after the field's type")
        if fname in dict(r.fields):
            raise Err("E-DUP", f"field `{fname}` is declared twice")
        self._check_type(ftype)
        r.fields.append((fname, ftype))

    def _choice_line(self, toks: list):
        ch = self.cur
        if self._is_end(toks):
            if not ch.cases:
                raise Err("E-SYNTAX", f"choice `{ch.name}` has no cases", ["add a line `CASE` or `CASE : TYPE`"])
            ch.hash = self._hash(ch)
            self.prog.choices[ch.name] = ch
            self.cur = None
            return
        c = Cur(toks)
        cname = _plain_name(c.next(), "a case name")
        ctype = None
        if c.accept(":"):
            ctype, c.i = T.parse(toks, c.i, self._types_in_scope())
            self._check_type(ctype)
        c.end("after the case")
        if cname in dict(ch.cases):
            raise Err("E-DUP", f"case `{cname}` is declared twice")
        ch.cases.append((cname, ctype))

    def _types_in_scope(self) -> set:
        return self.prog.named_types()

    def _check_type(self, t: tuple):
        if t[0] == "map" and T.resolve(t[1], {}) not in KEY_TYPES and t[1][0] != "var":
            raise Err("E-TYPE", f"map keys must be int, text or bool, not `{T.show(t[1])}`")
        if t[0] not in T.SIMPLE and t[0] not in ("named", "var"):
            for a in t[1:]:
                self._check_type(a)

    # ------------------------------------------------------------ unit header

    def _unit_line(self, toks: list):
        u = self.cur
        if u.pending:
            return self._arm_line(toks)
        if self._is_end(toks):
            return self._unit_end()
        if len(toks) > 1 and toks[1] == ("op", ":") and toks[0][0] == "name" and toks[0][1] not in KEYWORDS:
            return self._step_line(toks)
        c = Cur(toks)
        kind, word = c.next()
        if word not in HEADER_ORDER:
            if word in ("give", "check", "do") or (kind == "name" and c.is_word("=")):
                raise Err("E-SYNTAX", "a step must start with a label",
                          [f"write `s{len(u.steps)}: " + " ".join(_tok(t) for t in toks) + "`"])
            raise Err("E-SYNTAX", f"`{_tok((kind, word))}` cannot start a line inside a unit",
                      ["header lines start with: " + ", ".join(HEADER_ORDER),
                       "steps look like `LABEL: NAME = ...`, `LABEL: give ...`, `LABEL: check ...` or `LABEL: do ...`"])
        if u.steps:
            raise Err("E-ORDER", f"`{word}` belongs in the header, before the first step")
        if word in ("ensure", "example") and u.ret is None:
            raise Err("E-ORDER", f"`gives` must come before `{word}`", ["move the `gives TYPE` line above this one"])
        if word == "takes" and (u.examples or u.requires or u.ensures or u.shrinks):
            raise Err("E-ORDER", "`takes` lines come before `shrinks`, `require`, `ensure` and `example`",
                      ["move this line up, next to the other `takes` lines"])
        getattr(self, "_h_" + word)(c, toks)

    def _h_why(self, c: Cur, toks: list):
        u = self.cur
        if u.why is not None:
            raise Err("E-DUP", "a unit has exactly one `why` line")
        kind, val = c.next()
        if kind != "string":
            raise Err("E-SYNTAX", "`why` is followed by text in double quotes")
        c.end("after the `why` text")
        u.why = val

    def _h_takes(self, c: Cur, toks: list):
        u = self.cur
        name = _plain_name(c.next(), "an input name after `takes`")
        c.expect(":", f"after `takes {name}`")
        t, c.i = T.parse(toks, c.i, self._types_in_scope())
        c.end("after the input's type")
        self._check_type(t)
        self._new_name(name)
        u.params.append((name, t))
        u.scope[name] = t

    def _h_gives(self, c: Cur, toks: list):
        u = self.cur
        if u.ret is not None:
            raise Err("E-DUP", "a unit has exactly one `gives` line")
        t, c.i = T.parse(toks, c.i, self._types_in_scope())
        self._check_type(t)
        if c.accept("or"):
            c.expect("fault", "after `or`")
            u.fallible = True
        c.end("after the `gives` type")
        u.ret = t

    def _h_needs(self, c: Cur, toks: list):
        u = self.cur
        if u.needs:
            raise Err("E-DUP", "a unit has at most one `needs` line", ["list all effects on one line, separated by commas"])
        while True:
            words = []
            while not c.at_end() and not c.is_word(","):
                words.append(str(c.next()[1]))
            eff = " ".join(words)
            if eff not in EFFECTS:
                raise Err("E-NAME", f"`{eff}` is not an effect",
                          ["effects are: " + ", ".join(EFFECTS)])
            u.needs.add(eff)
            if not c.accept(","):
                break

    def _h_shrinks(self, c: Cur, toks: list):
        u = self.cur
        kind, path = c.next()
        c.end("after `shrinks`")
        head, *fields = str(path).split(".")
        t = dict(u.params).get(head) if kind == "name" else None
        if t is None:
            raise Err("E-NAME", f"`{head}` is not an input of this unit",
                      [f"inputs are: {', '.join(n for n, _ in u.params) or '(none)'}"])
        for f in fields:
            ft = self._field(t, f)
            if ft is None:
                raise Err("E-NAME", f"`{T.show(t)}` has no field `{f}`", self._field_fix(t))
            t = ft
        if t != T.INT and t != T.TEXT and t[0] != "seq":
            raise Err("E-TYPE", f"`shrinks` needs an int, text or seq; `{path}` is `{T.show(t)}`",
                      ["name an int, text or seq input, or a field of a record input: `shrinks INPUT.FIELD`"])
        u.shrinks = path

    def _h_require(self, c: Cur, toks: list):
        expr = self._condition(c, allow_result=False)
        self.cur.requires.append((expr, " ".join(_tok(t) for t in toks[1:])))

    def _h_ensure(self, c: Cur, toks: list):
        expr = self._condition(c, allow_result=True)
        self.cur.ensures.append((expr, " ".join(_tok(t) for t in toks[1:])))

    def _h_example(self, c: Cur, toks: list):
        u = self.cur
        if u.needs:
            raise Err("E-EXAMPLE", "examples are only allowed on units with no `needs`")
        args, subst = [], {}
        for pname, ptype in u.params:
            if c.is_word("gives") or c.at_end():
                raise Err("E-ARITY", f"example gives {len(args)} inputs; the unit takes {len(u.params)}")
            atom, at = self._atom(c, subst, literal_only=True)
            self._unify(at, ptype, subst, f"example input `{pname}`")
            args.append(atom)
        if not c.is_word("gives"):
            raise Err("E-ARITY", f"example has more inputs than the unit takes ({len(u.params)})",
                      ["after the inputs write `gives EXPECTED`"])
        c.next()
        if c.accept("fault"):
            kind, code = c.next()
            if kind != "string":
                raise Err("E-SYNTAX", "`fault` is followed by a code in double quotes")
            if not u.fallible:
                raise Err("E-FAULT", "this unit cannot fault", ["declare `gives TYPE or fault`"])
            expected = ("fault", code)
        else:
            expected, et = self._atom(c, subst, literal_only=True)
            self._unify(et, u.ret, subst, "example result")
        c.end("after the example")
        u.examples.append((args, expected, self.lineno))

    def _condition(self, c: Cur, allow_result: bool, subst: Optional[dict] = None, stop: tuple = ()) -> tuple:
        """`VALUE` or `VALUE COMPARISON VALUE`, of type bool."""
        subst = self.cur.subst if subst is None else subst
        if self.nested:
            self._allow_result = allow_result
            e, t = self._expr(c, subst, stop, 0)
            self._allow_result = False
            if not c.at_end() and c.peek()[1] not in stop:
                raise Err("E-SYNTAX", f"unexpected `{_tok(c.peek())}` after the condition")
            if self._fall:
                raise Err("E-FAULT", "a condition cannot hold something that can fault",
                          ["bind it in its own step with `else give fault` or `else VALUE`, then use the name"])
            self._unify(t, T.BOOL, subst, "condition")
            return e
        left, lt = self._atom(c, subst, allow_result=allow_result)
        if c.at_end() or c.peek()[1] in stop:
            self._unify(lt, T.BOOL, subst, "condition")
            return ("atom", left)
        kind, op_ = c.next()
        if op_ not in COMPARES:
            raise Err("E-SYNTAX", f"expected a comparison ({', '.join(sorted(COMPARES))}), found `{_tok((kind, op_))}`",
                      ["a condition is `VALUE` or `VALUE COMPARISON VALUE`; compute anything larger in a step first"])
        right, rt = self._atom(c, subst, allow_result=allow_result)
        if not c.at_end() and c.peek()[1] not in stop:
            raise Err("E-SYNTAX", f"unexpected `{_tok(c.peek())}` after the condition",
                      ["a condition is `VALUE` or `VALUE COMPARISON VALUE`; compute anything larger in a step first"])
        self._binop_type(op_, lt, rt, subst)
        return ("binop", op_, left, right)

    # ------------------------------------------------------------ steps

    def _new_name(self, name: str):
        u = self.cur
        if name in u.scope:
            raise Err("E-DUP", f"`{name}` already has a value; names are given a value once",
                      ["choose a new name for the new value"])
        taken = self.prog.taken(name)
        if taken or name == u.name:
            raise Err("E-DUP", f"`{name}` is already a {taken or 'unit'}", ["choose a different name"])

    def _step_line(self, toks: list):
        u = self.cur
        if u.ret is None:
            raise Err("E-ORDER", "the header needs a `gives TYPE` line before the first step")
        if u.done:
            raise Err("E-UNREACHABLE", "nothing can run after an unconditional `give`", [f"write `end {u.name}`"])
        label = toks[0][1]
        if "." in label:
            raise Err("E-SYNTAX", f"`{label}` is not a label")
        if any(s.label == label for s in u.steps):
            raise Err("E-DUP", f"label `{label}` is already used in this unit", [f"use `s{len(u.steps)}`"])
        c = Cur(toks)
        c.i = 2
        kind, word = c.peek()
        if word == "give":
            c.next()
            self._give(c, label)
        elif word == "check":
            c.next()
            expr = self._condition(c, allow_result=False)
            u.steps.append(Step(label, "check", self.lineno, expr=expr, code=" ".join(_tok(t) for t in toks[3:])))
        elif word == "do":
            c.next()
            subst = u.subst
            expr, t, fallible = self._simple(c, subst, stop=("else",))
            self._unify(t, T.NOTHING, subst, "`do`",
                        fixes=[f"this gives a `{T.show(T.resolve(t, subst))}`; bind it: `{label}: NAME = ...`"])
            handler = self._handler(c, subst, T.NOTHING, fallible)
            c.end("after the step")
            u.steps.append(Step(label, "do", self.lineno, expr=expr, handler=handler))
        else:
            self._bind(c, label)

    def _give(self, c: Cur, label: str):
        u = self.cur
        subst = u.subst
        if c.accept("fault"):
            kind, code = c.next()
            if kind != "string":
                raise Err("E-SYNTAX", "`give fault` is followed by a code in double quotes, e.g. `give fault \"empty\"`")
            if not u.fallible:
                raise Err("E-FAULT", "this unit cannot fault", ["change the header to `gives TYPE or fault`"])
            step = Step(label, "give_fault", self.lineno, code=code)
        elif self.nested:
            e, t = self._expr(c, subst, ("when",), 0)
            if self._fall:
                raise Err("E-FAULT", "`give` cannot hold something that can fault",
                          ["bind it in its own step with `else give fault` or `else VALUE`, then give the name"])
            self._unify(t, u.ret, subst, "`give`")
            step = Step(label, "give", self.lineno, expr=e)
        else:
            atom, t = self._atom(c, subst)
            if not c.at_end() and not c.is_word("when"):
                raise Err("E-SYNTAX", f"`give` takes one value; found `{_tok(c.peek())}` after it",
                          [f"compute it in its own step first, then `give` the name"])
            self._unify(t, u.ret, subst, "`give`")
            step = Step(label, "give", self.lineno, expr=("atom", atom))
        if c.accept("when"):
            step.cond = self._condition(c, False, subst)
        else:
            u.done = True
        c.end("after `give`")
        u.steps.append(step)

    def _bind(self, c: Cur, label: str):
        u = self.cur
        name = _plain_name(c.next(), "a name, `give`, `check` or `do` after the label")
        declared = None
        if c.accept(":"):
            declared, c.i = T.parse(c.t, c.i, self._types_in_scope())
            self._check_type(declared)
        c.expect("=", f"after `{name}`")
        self._new_name(name)
        subst = u.subst
        if c.is_word("match"):
            c.next()
            atom, at = self._atom(c, subst)
            c.end("after `match VALUE`; each case goes on its own following line starting with `on`")
            at = T.resolve(at, subst)
            cases = self._cases_of(at)
            if cases is None:
                raise Err("E-TYPE", f"`match` needs a choice, maybe or loop value; this is `{T.show(at)}`")
            result = declared if declared is not None else T.fresh()
            u.pending = {"label": label, "name": name, "atom": atom, "cases": cases, "arms": [],
                         "result": result, "subst": subst, "line": self.lineno}
            return
        if c.is_word("when") and not self.nested:
            c.next()
            expr, t, fallible = self._when_chain(c, subst)
        else:
            expr, t, fallible = self._simple(c, subst, stop=("else",))
        if declared is not None:
            self._unify(t, declared, subst, f"`{name}`")
        handler = self._handler(c, subst, t, fallible)
        c.end("after the step")
        t = self._settle(t, subst, name)
        u.scope[name] = t
        u.steps.append(Step(label, "bind", self.lineno, name=name, expr=expr, type=t, handler=handler))

    def _when_chain(self, c: Cur, subst: dict):
        """Flat variant: `when C then A else B`, where B may itself be another `when ...`."""
        cond = self._condition(c, False, subst, stop=("then",))
        c.expect("then", "after the `when` condition")
        a, at, af = self._simple(c, subst, stop=("else",))
        c.expect("else", "after the `then` value")
        if c.accept("when"):
            b, bt, bf = self._when_chain(c, subst)
        else:
            b, bt, bf = self._simple(c, subst, stop=("else",))
        self._unify(bt, at, subst, "`else` value")
        return ("when", cond, a, b), at, af or bf

    def _settle(self, t: tuple, subst: dict, name: str) -> tuple:
        """A type may stay partly unknown here; later steps can pin it down. Checked when the unit closes."""
        t = T.resolve(t, subst)
        self._check_type(t)
        return t

    def _arm_line(self, toks: list):
        u = self.cur
        p = u.pending
        case, payload = p["cases"][len(p["arms"])]
        want = f"on {case} NAME then VALUE" if payload is not None else f"on {case} then VALUE"
        c = Cur(toks)
        if not c.accept("on"):
            raise Err("E-ARMS", f"`match` is not finished: the next line must handle case `{case}`", [f"write `{want}`"])
        got = c.next()[1]
        if got != case:
            raise Err("E-ARMS", f"cases are handled in their declared order; expected `{case}`, found `{got}`",
                      [f"write `{want}`"])
        subst = p["subst"]
        binder = None
        if payload is not None:
            if c.is_word("then"):
                raise Err("E-ARMS", f"case `{case}` carries a `{T.show(T.resolve(payload, subst))}`; name it", [f"write `{want}`"])
            binder = _plain_name(c.next(), "a name for the case's value")
            self._new_name(binder)
            u.scope[binder] = T.resolve(payload, subst)
        try:
            c.expect("then", f"in `{want}`")
            if c.is_word("when") and not self.nested:
                c.next()
                expr, t, fallible = self._when_chain(c, subst)
            else:
                expr, t, fallible = self._simple(c, subst, stop=("else",))
            self._unify(t, p["result"], subst, f"case `{case}`")
            handler = self._handler(c, subst, t, fallible)
            c.end("after the case")
        finally:
            if binder:
                del u.scope[binder]
        p["arms"].append((case, binder, expr, handler))
        if len(p["arms"]) == len(p["cases"]):
            t = self._settle(p["result"], subst, p["name"])
            u.scope[p["name"]] = t
            u.steps.append(Step(p["label"], "bind", p["line"], name=p["name"],
                                expr=("match", p["atom"], p["arms"]), type=t))
            u.pending = None

    def _cases_of(self, t: tuple) -> Optional[list]:
        if t[0] == "named" and t[1] in self.prog.choices:
            return list(self.prog.choices[t[1]].cases)
        if t[0] in BUILTIN_CHOICES:
            return [(case, t[1 + idx] if idx is not None else None) for case, idx in BUILTIN_CHOICES[t[0]]]
        return None

    def _handler(self, c: Cur, subst: dict, t: tuple, fallible: bool) -> Optional[tuple]:
        u = self.cur
        fallible = fallible or self._fall
        self._fall = False
        if not c.is_word("else"):
            if fallible:
                fixes = ["add `else give fault` to pass the fault to this unit's caller"
                         + ("" if u.fallible else " (and change the header to `gives TYPE or fault`)"),
                         "add `else VALUE` to use a fallback value instead"]
                raise Err("E-FAULT", "this step can fault and the fault is not handled", fixes)
            return None
        c.next()
        if not fallible:
            raise Err("E-FAULT", "nothing in this step can fault, so `else` has nothing to handle", ["remove the `else` part"])
        if c.accept("give"):
            c.expect("fault", "after `else give`")
            if not u.fallible:
                raise Err("E-FAULT", "this unit cannot fault, so it cannot pass a fault on",
                          ["change the header to `gives TYPE or fault`", "or use a fallback: `else VALUE`"])
            if c.peek()[0] == "string":
                return ("give_fault", c.next()[1])
            return ("give_fault", None)
        atom, at = self._atom(c, subst)
        self._unify(at, t, subst, "`else` fallback")
        return ("default", atom)

    # ------------------------------------------------------------ expressions

    def _unify(self, got: tuple, want: tuple, subst: dict, what: str, fixes: Optional[list] = None):
        if not T.unify(got, want, subst):
            raise Err("E-TYPE", f"{what} should be `{T.show(T.resolve(want, subst))}` but is `{T.show(T.resolve(got, subst))}`", fixes)

    def _atom(self, c: Cur, subst: dict, literal_only: bool = False, allow_result: bool = False):
        """Parse one value. Returns (atom, type)."""
        u = self.cur
        kind, val = c.next()
        if kind == "int":
            return ("lit", val), T.INT
        if kind == "string":
            return ("lit", val), T.TEXT
        if kind == "op" and val == "(":
            if not self.nested:
                raise Err("E-SYNTAX", "Lathe has no parentheses: a step applies one operation to plain values",
                          ["compute the inner part in its own step, give it a name, and use the name here"])
            e, t = self._expr(c, subst, (), 0)
            c.expect(")", "to close the parenthesis")
            return self._as_atom(e), t
        if kind == "op" and val == "[":
            items, elem = [], T.fresh()
            while not c.accept("]"):
                if items:
                    c.expect(",", "between list items")
                if self.nested and not literal_only:
                    e, it = self._expr(c, subst, (), 0)
                    item = self._as_atom(e)
                else:
                    item, it = self._atom(c, subst, literal_only, allow_result)
                self._unify(it, elem, subst, "list item")
                items.append(item)
            return ("list", items), ("seq", elem)
        if kind == "name":
            if val in ("true", "false"):
                return ("lit", val == "true"), T.BOOL
            if val == "nothing":
                return ("lit", None), T.NOTHING
            if val == "result" and (allow_result or self._allow_result):
                return ("result",), u.ret
            if literal_only:
                raise Err("E-SYNTAX", f"only literal values are allowed here, not `{val}`",
                          ["use numbers, \"text\", true, false or [lists] of those"])
            if val in KEYWORDS:
                raise Err("E-SYNTAX", f"expected a value, found keyword `{val}`")
            head, *path = val.split(".")
            if head in u.scope and val not in BUILTINS:
                atom, t = ("name", head), u.scope[head]
                for f in path:
                    t = T.resolve(t, subst)
                    ftype = self._field(t, f)
                    if ftype is None:
                        raise Err("E-NAME", f"`{T.show(t)}` has no field `{f}`", self._field_fix(t))
                    atom, t = ("field", atom, f), ftype
                return atom, t
            if val in BUILTINS:
                b = BUILTINS[val]
                if not b.params and not b.effect and not b.fallible:
                    return ("const", val), T.instantiate(b.ret, {})
                raise Err("E-SYNTAX", f"`{val}` is an operation, not a value; a step applies one operation to plain values",
                          [f"give its result a name in an earlier step, then use the name here"])
            if head in self.prog.choices and len(path) == 1:
                cases = dict(self.prog.choices[head].cases)
                if path[0] in cases and cases[path[0]] is None:
                    return ("case", head, path[0], None), ("named", head)
                if path[0] in cases:
                    raise Err("E-SYNTAX", f"`{val}` carries a value, so it is an operation, not a plain value",
                              ["give it a name in an earlier step, then use the name here"])
            if val in self.prog.units or val == u.name:
                raise Err("E-SYNTAX", f"`{val}` is a unit; calling it is an operation, not a plain value",
                          ["give the call's result a name in its own step, then use the name here"]
                          if not self.nested else ["put the call in parentheses"])
            self._unknown(val)
        shown = f"`{_tok((kind, val))}`" if val is not None else "the end of the line"
        raise Err("E-SYNTAX", f"expected a value, found {shown}")

    def _unknown(self, val: str):
        u = self.cur
        head = val.split(".")[0]
        if head in self.prog.poisoned:
            raise Err("E-BLOCKED", f"`{head}` has a fault of its own; fix it first")
        pool = list(u.scope) + list(self.prog.units) + list(BUILTINS)
        pool += [f"{ch.name}.{case}" for ch in self.prog.choices.values() for case, _ in ch.cases]
        near = _near(val, pool)
        fixes = ["did you mean: " + ", ".join(near)] if near else []
        if "." in val and head in NAMESPACES:
            fixes.append(f"`ask find \"...\"` lists operations; `{head}` has: "
                         + ", ".join(n for n in BUILTINS if n.startswith(head + ".")))
        elif not near:
            fixes.append("names must be given a value in an earlier step; in scope: " + (", ".join(u.scope) or "(nothing)"))
        raise Err("E-NAME", f"`{val}` is not known here", fixes)

    def _field(self, t: tuple, f: str) -> Optional[tuple]:
        if t[0] == "named" and t[1] in self.prog.records:
            return dict(self.prog.records[t[1]].fields).get(f)
        if t[0] == "pair":
            return {"first": t[1], "second": t[2]}.get(f)
        return None

    def _field_fix(self, t: tuple) -> list:
        if t[0] == "named" and t[1] in self.prog.records:
            return ["fields are: " + ", ".join(n for n, _ in self.prog.records[t[1]].fields)]
        if t[0] == "pair":
            return ["a pair has `.first` and `.second`"]
        if self._cases_of(t):
            return ["read a choice with `match`"]
        return []

    PREC = {"or": 1, "and": 2, "==": 3, "!=": 3, "<": 3, "<=": 3, ">": 3, ">=": 3,
            "+": 4, "-": 4, "*": 5, "/": 5, "%": 5}

    @staticmethod
    def _as_atom(e: tuple) -> tuple:
        return e[1] if e[0] == "atom" else ("expr", e)

    def _expr(self, c: Cur, subst: dict, stop: tuple, minp: int):
        """Nested variant: a full expression with the usual precedence. Returns (expr, type)."""
        left, lt, fallible = self._operand(c, subst, stop)
        self._fall = self._fall or fallible
        while True:
            k, v = c.peek()
            if c.at_end() or v in stop or k == "string" or v not in self.PREC or self.PREC[v] < minp:
                return left, lt
            c.next()
            right, rt = self._expr(c, subst, stop, self.PREC[v] + 1)
            lt = self._binop_type(v, lt, rt, subst)
            left = ("binop", v, self._as_atom(left), self._as_atom(right))

    def _simple(self, c: Cur, subst: dict, stop: tuple = ()):
        """Flat variant: one operation applied to plain values. Returns (expr, type, fallible)."""
        if self.nested:
            e, t = self._expr(c, subst, stop, 0)
            return e, t, False
        left, lt, fallible = self._operand(c, subst, stop)
        if left[0] != "atom":
            return left, lt, fallible
        left = left[1]
        k2, v2 = c.peek()
        if c.at_end() or v2 in stop:
            return ("atom", left), lt, False
        if v2 not in BINOPS or k2 == "string":
            hint = []
            if v2 == "=":
                hint = ["equality is written `==`"]
            elif left[0] == "name":
                hint = [f"`{left[1]}` is a value, not an operation; to call a unit or operation put its name first"]
            raise Err("E-SYNTAX", f"unexpected `{_tok((k2, v2))}` after a value", hint
                      + ["a step holds one operation; split larger expressions into several steps"])
        c.next()
        right, rt = self._atom(c, subst)
        if not c.at_end() and c.peek()[1] not in stop:
            raise Err("E-SYNTAX", f"a step holds one operation; found more after `{_tok(c.t[c.i - 1])}`",
                      ["give the first part a name in its own step, then use that name"])
        return ("binop", v2, left, right), self._binop_type(v2, lt, rt, subst), False

    def _operand(self, c: Cur, subst: dict, stop: tuple = ()):
        """A call, constructor, `not`, or plain value. Returns (expr, type, fallible)."""
        u = self.cur
        kind, val = c.peek()
        if kind == "name":
            if val == "not":
                c.next()
                if self.nested:
                    e, t = self._expr(c, subst, stop, 3)
                    atom = self._as_atom(e)
                else:
                    atom, t = self._atom(c, subst)
                self._unify(t, T.BOOL, subst, "`not`")
                return ("not", atom), T.BOOL, False
            if val == "when" and self.nested:
                c.next()
                cond, ct = self._expr(c, subst, ("then",), 0)
                self._unify(ct, T.BOOL, subst, "`when` condition")
                c.expect("then", "after the `when` condition")
                a, at = self._expr(c, subst, stop + ("else",), 0)
                c.expect("else", "after the `then` value")
                b, bt = self._expr(c, subst, stop + ("else",), 0)
                self._unify(bt, at, subst, "`else` value")
                return ("when", cond, a, b), at, False
            if val == "new" and "new" not in u.scope:
                c.next()
                return self._new(c, subst)
            if val == "give":
                c.next()
                c.expect("fault", "after `give` (only `give fault \"CODE\"` may be used as a value)")
                kind2, code = c.next()
                if kind2 != "string":
                    raise Err("E-SYNTAX", "`give fault` is followed by a code in double quotes")
                if not u.fallible:
                    raise Err("E-FAULT", "this unit cannot fault", ["change the header to `gives TYPE or fault`"])
                return ("raise", code), T.fresh(), False
            if val == "repeat":
                c.next()
                return self._repeat(c, subst)
            if val in ("when", "match"):
                raise Err("E-SYNTAX", f"`{val}` cannot be nested inside another `when` or `match`",
                          ["move it into its own unit and call that unit here"])
            head = val.split(".")[0]
            if head not in u.scope or val in BUILTINS:
                if val in BUILTINS and (BUILTINS[val].params or BUILTINS[val].effect or BUILTINS[val].fallible):
                    c.next()
                    return self._call_builtin(c, subst, BUILTINS[val], stop)
                if val in self.prog.units or val == u.name:
                    c.next()
                    return self._call_unit(c, subst, val, stop)
                if head in self.prog.choices and "." in val:
                    cases = dict(self.prog.choices[head].cases)
                    case = val.split(".", 1)[1]
                    if case not in cases:
                        raise Err("E-NAME", f"choice `{head}` has no case `{case}`", ["cases are: " + ", ".join(cases)])
                    if cases[case] is not None:
                        c.next()
                        if c.at_end() or c.peek()[1] in stop or c.peek()[1] == ")":
                            raise Err("E-ARITY", f"`{val}` carries a `{T.show(cases[case])}`", [f"write `{val} VALUE`"])
                        atom, t = self._atom(c, subst)
                        self._unify(t, cases[case], subst, f"value of `{val}`")
                        return ("case", head, case, atom), ("named", head), False
                elif head in self.prog.records and "." not in val:
                    raise Err("E-SYNTAX", f"`{val}` is a record type", [f"build one with `new {val} FIELD = VALUE, ...`"])
        left, lt = self._atom(c, subst)
        return ("atom", left), lt, False

    def _binop_type(self, op_: str, lt: tuple, rt: tuple, subst: dict) -> tuple:
        lt, rt = T.resolve(lt, subst), T.resolve(rt, subst)
        if op_ in ("+", "-", "*", "/", "%"):
            if lt == T.TEXT and op_ == "+":
                raise Err("E-TYPE", "`+` adds numbers; these are text", ["use `text.concat A B` or `text.join [A, B, ...] \"\"`"])
            self._unify(lt, T.INT, subst, f"left side of `{op_}`")
            self._unify(rt, T.INT, subst, f"right side of `{op_}`")
            return T.INT
        if op_ in ("and", "or"):
            self._unify(lt, T.BOOL, subst, f"left side of `{op_}`")
            self._unify(rt, T.BOOL, subst, f"right side of `{op_}`")
            return T.BOOL
        self._unify(rt, lt, subst, f"right side of `{op_}`")
        if op_ not in ("==", "!="):
            side = T.resolve(lt, subst)
            if side not in ORDERED_TYPES:
                raise Err("E-TYPE", f"`{op_}` orders int or text values, not `{T.show(side)}`")
        return T.BOOL

    def _args(self, c: Cur, subst: dict, stop: tuple) -> list:
        args = []
        while not c.at_end() and c.peek()[1] not in stop:
            if self.nested and c.peek()[0] != "string" and (c.peek()[1] in self.PREC or c.peek()[1] in (")", ",", "]")):
                break
            if c.peek()[0] == "op" and c.peek()[1] not in ("[", "("):
                raise Err("E-SYNTAX", f"unexpected `{c.peek()[1]}` among the inputs of an operation",
                          ["a step holds one operation; compute each input in its own step first"])
            args.append(self._atom(c, subst))
        return args

    def _call_builtin(self, c: Cur, subst: dict, b, stop: tuple):
        u = self.cur
        args = self._args(c, subst, stop + ("with",))
        if len(args) != len(b.params):
            raise Err("E-ARITY", f"`{b.name}` takes {len(b.params)} input(s), found {len(args)}", ["form: " + b.signature()])
        mapping = {}
        for i, ((atom, at), pt) in enumerate(zip(args, b.params)):
            self._unify(at, T.instantiate(pt, mapping), subst, f"input {i + 1} of `{b.name}`", ["form: " + b.signature()])
        fallible, with_unit, with_args = b.fallible, None, []
        if b.with_:
            if not c.accept("with"):
                raise Err("E-ARITY", f"`{b.name}` needs `with UNIT`", ["form: " + b.signature()])
            handed = [T.instantiate(t, mapping) for t in b.with_[0]]
            with_unit, with_args, wf = self._with(c, subst, handed, T.instantiate(b.with_[1], mapping), stop, b.name)
            fallible = fallible or wf
        elif c.is_word("with"):
            raise Err("E-ARITY", f"`{b.name}` does not take `with`", ["form: " + b.signature()])
        self._constraints(b, mapping, subst)
        if b.effect:
            self._use_effect(b.effect)
        self._fall = self._fall or fallible
        return ("builtin", b.name, [a for a, _ in args], with_unit, with_args), T.instantiate(b.ret, mapping), fallible

    def _with(self, c: Cur, subst: dict, handed: list, want_ret: tuple, stop: tuple, opname: str):
        """The part after `with`: a unit defined earlier, or a built-in operation, plus extra inputs."""
        u = self.cur
        kind, name = c.next()
        if kind == "name" and name in BUILTINS and BUILTINS[name].with_ is None:
            b = BUILTINS[name]
            mapping = {}
            params = [(f"{i + 1}", T.instantiate(p, mapping)) for i, p in enumerate(b.params)]
            ret, fallible = T.instantiate(b.ret, mapping), b.fallible
            needs, shape = ({b.effect} if b.effect else set()), b.signature()
        elif kind == "name" and name in self.prog.units:
            w = self.prog.units[name]
            params, ret, fallible, needs, shape, b = w.params, w.ret, w.fallible, w.needs, self._params(w), None
            u.deps.add(name)
        else:
            if name in self.prog.poisoned:
                raise Err("E-BLOCKED", f"`{name}` has a fault of its own; fix it first")
            near = _near(str(name), list(self.prog.units) + list(BUILTINS))
            raise Err("E-NAME", f"`with` must be followed by a unit defined earlier or a built-in operation; `{name}` is neither",
                      (["did you mean: " + ", ".join(near)] if near else [])
                      + ["define a small unit above this one and name it here"])
        extras = self._args(c, subst, stop)
        if len(params) != len(handed) + len(extras):
            raise Err("E-ARITY", f"`{opname}` hands `{name}` {len(handed)} value(s) and this line adds {len(extras)}; "
                                 f"`{name}` takes {len(params)}", ["its inputs are: " + shape])
        for ht, (pn, pt) in zip(handed, params):
            self._unify(pt, ht, subst, f"input {pn} of `{name}`")
        for (atom, at), (pn, pt) in zip(extras, params[len(handed):]):
            self._unify(at, pt, subst, f"input {pn} of `{name}`")
        self._unify(ret, want_ret, subst, f"what `{name}` gives")
        if b is not None:
            self._constraints(b, mapping, subst)
        for eff in needs:
            self._use_effect(eff)
        return name, [a for a, _ in extras], fallible

    def _constraints(self, b, mapping: dict, subst: dict):
        for var in b.constraints:
            vt = T.resolve(T.instantiate(("var", var), mapping), subst)
            if vt[0] != "var" and vt not in ORDERED_TYPES:
                raise Err("E-TYPE", f"`{b.name}` works on int or text values, not `{T.show(vt)}`",
                          ["use `seq.sort_by ... with UNIT` to order by an int or text key"] if b.name == "seq.sort" else [])

    def _params(self, w: Unit) -> str:
        return ", ".join(f"{n} : {T.show(t)}" for n, t in w.params) or "(none)"

    def _call_unit(self, c: Cur, subst: dict, name: str, stop: tuple):
        u = self.cur
        args = self._args(c, subst, stop)
        w = u if name == u.name else self.prog.units[name]
        if len(args) != len(w.params):
            raise Err("E-ARITY", f"`{name}` takes {len(w.params)} input(s), found {len(args)}",
                      ["its inputs are: " + self._params(w)])
        for (atom, at), (pn, pt) in zip(args, w.params):
            self._unify(at, pt, subst, f"input `{pn}` of `{name}`")
        if w is u:
            if not u.shrinks:
                raise Err("E-SHRINKS", "a unit that calls itself must say which input gets smaller each time",
                          ["add a header line `shrinks INPUT` after `gives` (INPUT is an int that decreases toward 0, or a text/seq that gets shorter, in every self-call)",
                           "if no input always gets smaller, add an extra int input that counts down, or use `repeat ... limit N`"])
            u.calls_self = True
        else:
            for eff in w.needs:
                self._use_effect(eff)
            u.deps.add(name)
        self._fall = self._fall or w.fallible
        return ("call", name, [a for a, _ in args]), w.ret, w.fallible

    def _use_effect(self, eff: str):
        u = self.cur
        if eff not in u.needs:
            have = ", ".join(sorted(u.needs | {eff}))
            raise Err("E-EFFECT", f"this step needs `{eff}`, which the unit does not declare",
                      [f"header line: `needs {have}` (after `gives`)"])
        u.used.add(eff)

    def _new(self, c: Cur, subst: dict):
        kind, rname = c.next()
        if rname not in self.prog.records:
            near = _near(str(rname), self.prog.records)
            raise Err("E-NAME", f"`{rname}` is not a record", ["did you mean: " + ", ".join(near)] if near else [])
        rec = self.prog.records[rname]
        form = f"new {rname} " + ", ".join(f"{n} = VALUE" for n, _ in rec.fields)
        fields = []
        for i, (fname, ftype) in enumerate(rec.fields):
            if i:
                c.expect(",", f"between fields; form: `{form}`")
            if not c.accept(fname):
                raise Err("E-FIELDS", f"fields are set in their declared order; expected `{fname}`", [f"form: `{form}`"])
            c.expect("=", f"after field `{fname}`")
            if self.nested:
                e, at = self._expr(c, subst, ("else", "then", "when", "limit"), 0)
                atom = self._as_atom(e)
            else:
                atom, at = self._atom(c, subst)
            self._unify(at, ftype, subst, f"field `{fname}`")
            fields.append((fname, atom))
        return ("new", rname, fields), ("named", rname), False

    def _repeat(self, c: Cur, subst: dict):
        form = "repeat START with UNIT limit COUNT else ..."
        state, st = self._atom(c, subst)
        if not c.accept("with"):
            raise Err("E-SYNTAX", "`repeat` needs `with UNIT`", [f"form: `{form}`"])
        result = T.fresh()
        name, extras, _ = self._with(c, subst, [st], ("loop", st, result), ("limit",), "repeat")
        if not c.accept("limit"):
            raise Err("E-SYNTAX", "`repeat` must state the most rounds it may take", [f"form: `{form}`"])
        limit, lt = self._atom(c, subst)
        self._unify(lt, T.INT, subst, "`limit`")
        self._fall = True
        return ("repeat", state, name, extras, limit), result, True

    # ------------------------------------------------------------ closing a unit

    def _unit_end(self):
        u = self.cur
        if not u.done:
            raise Err("E-GIVE", "a unit's last step must be an unconditional `give`",
                      [f"add `s{len(u.steps)}: give VALUE` before `end {u.name}`"])
        unused = u.needs - u.used
        if unused:
            keep = ", ".join(sorted(u.used))
            raise Err("E-EFFECT", f"the unit declares `needs {', '.join(sorted(unused))}` but never uses it",
                      [f"change the header to `needs {keep}`" if keep else "remove the `needs` line"])
        if u.shrinks and not u.calls_self:
            raise Err("E-SHRINKS", "`shrinks` is only for units that call themselves", ["remove the `shrinks` line"])
        for st in u.steps:
            if st.type is not None:
                st.type = T.resolve(st.type, u.subst)
                if T.has_var(st.type):
                    raise Err("E-AMBIG", f"the type of `{st.name}` (step {st.label}) cannot be worked out: `{T.show(st.type)}`",
                              [f"state it: `{st.name} : TYPE = ...`, for example `{st.name} : seq of int = []`"])
                self._check_type(st.type)
        u.hash = self._hash(u)
        if self.run_examples and u.examples:
            self._run_examples(u)
        u.evidence = f"observed, {len(u.examples)} example(s) pass" if u.examples else "assumed"
        self.prog.units[u.name] = u
        self.cur = None

    def _run_examples(self, u: Unit):
        from .interp import Interp, Runtime, fmt  # local import: interp depends on this module

        self.prog.units[u.name] = u
        try:
            for args, expected, line in u.examples:
                interp = Interp(self.prog, Runtime.sealed(), budget=2_000_000)
                shown = " ".join(fmt(interp.atom(a, {})) for a in args)
                try:
                    got = fmt(interp.call(u.name, [interp.atom(a, {}) for a in args]))
                except Fault as f:
                    got = f'fault "{f.code}"'
                except Trap as t:
                    where = f" at {t.where[0]} {t.where[1]}" if t.where else ""
                    raise Err("E-EXAMPLE", f"the example on line {line} stopped with a trap{where}: {t.message}",
                              ["fix the steps, or fix the example if it is wrong"])
                want = f'fault "{expected[1]}"' if expected[0] == "fault" else fmt(interp.atom(expected, {}))
                if got != want:
                    raise Err("E-EXAMPLE", f"the example on line {line} fails: `{u.name} {shown}` gives {got}, not {want}",
                              ["fix the steps, or fix the example if it is wrong"])
        finally:
            del self.prog.units[u.name]

    def _hash(self, d) -> str:
        h = hashlib.sha256()
        for toks in d.lines:
            if toks[0] == ("name", "why"):
                continue
            h.update(repr(toks).encode())
        deps = sorted(getattr(d, "deps", ()))
        for name in deps:
            h.update(self.prog.units[name].hash.encode())
        for name in sorted(self.prog.named_types()):
            if any(("name", name) in toks for toks in d.lines):
                h.update((self.prog.records.get(name) or self.prog.choices.get(name)).hash.encode())
        return h.hexdigest()[:12]


def _logical(source: str) -> list:
    """[(line number, text)], with lines that start with `else` or `then` joined to the line before."""
    out = []
    for no, text in enumerate(source.splitlines(), 1):
        first = text.strip().split(" ", 1)[0]
        if first in ("else", "then") and out and out[-1][1].strip():
            out[-1] = (out[-1][0], out[-1][1].split(" --")[0] + " " + text.strip())
        else:
            out.append((no, text))
    return out


def _ordered(lines: list) -> list:
    """Order whole definitions so that each comes after the definitions it mentions.

    A file may list definitions in any order; the checker itself still never looks forward.
    Returns [(line number, text)]. Definitions in a cycle keep their written order.
    """
    chunks, current = [], None           # chunk: [name, [(lineno, text)], mentioned names]
    for no, text in lines:
        try:
            toks = lex(text)
        except LexError:
            toks = []
        words = {str(v).split(".")[0] for k, v in toks if k == "name"}
        starts = len(toks) == 2 and toks[0][1] in ("record", "choice", "unit") and toks[0][0] == "name"
        if starts or current is None:
            current = [toks[1][1] if starts else None, [], set()]
            chunks.append(current)
        current[1].append((no, text))
        current[2] |= words
        if toks[:1] == [("name", "end")]:
            current = None
    by_name = {}
    for ch in chunks:
        if ch[0] is not None:
            by_name.setdefault(ch[0], ch)
    out, state = [], {}

    def visit(ch):
        key = id(ch)
        if state.get(key):
            return
        state[key] = 1
        for name in sorted(ch[2], key=lambda n: by_name[n][1][0][0] if n in by_name else 0):
            dep = by_name.get(name)
            if dep is not None and dep is not ch:
                visit(dep)
        out.extend(ch[1])

    for ch in chunks:
        visit(ch)
    return out


def check_source(source: str, run_examples: bool = True, nested: bool = False) -> Session:
    s = Session(run_examples=run_examples, nested=nested)
    last = 0
    for no, text in _ordered(_logical(source)):
        s.feed(text, no)
        last = max(last, no)
    s.lineno = last
    s.finish()
    s.verdicts.sort(key=lambda v: (v.code == "E-BLOCKED", v.line))
    return s
