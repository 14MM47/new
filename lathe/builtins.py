"""The standard vocabulary: every built-in operation, its signature and its meaning.

Signatures use single capital letters for unknown types. `with_` describes the
unit an operation takes after the word `with`: (types it is handed, type it gives).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional

from . import types as T
from .lexer import lex

INT_MIN, INT_MAX = -(2**63), 2**63 - 1


class Fault(Exception):
    """An expected failure. Part of a unit's type; the caller must handle it."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class Trap(Exception):
    """A bug: the program stops. Never part of a type."""

    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind
        self.message = message
        self.where = None


def chk(n: int) -> int:
    if n < INT_MIN or n > INT_MAX:
        raise Trap("overflow", "integer result does not fit in 64 bits")
    return n


class Rec(dict):
    """A record or pair value. `kind` is the record's name."""

    def __init__(self, kind: str, fields):
        super().__init__(fields)
        self.kind = kind


def pair(a, b) -> Rec:
    return Rec("pair", {"first": a, "second": b})


def some(v):
    return ("some", v)


NONE = ("none", None)


@dataclass
class Builtin:
    name: str
    params: list
    ret: tuple
    impl: Callable
    doc: str
    with_: Optional[tuple] = None
    fallible: bool = False
    effect: Optional[str] = None
    constraints: dict = field(default_factory=dict)

    def signature(self) -> str:
        parts = [T.show(p) for p in self.params]
        s = self.name + "".join(" " + _wrap(p) for p in parts)
        if self.with_:
            handed = " ".join(_wrap(T.show(t)) for t in self.with_[0])
            s += f" with UNIT[{handed} -> {T.show(self.with_[1])}]"
        s += " -> " + T.show(self.ret)
        if self.fallible:
            s += " or fault"
        return s


def _wrap(s: str) -> str:
    return f"({s})" if " " in s else s


def _ty(src: str) -> tuple:
    t, pos = T.parse(lex(src), 0, set(), vars_ok=True)
    return t


BUILTINS: dict = {}


def op(name, params, ret, doc, with_=None, fallible=False, effect=None, **constraints):
    def register(impl):
        w = None
        if with_:
            w = ([_ty(p) for p in with_[0]], _ty(with_[1]))
        BUILTINS[name] = Builtin(
            name, [_ty(p) for p in params], _ty(ret), impl, doc, w, fallible, effect, constraints
        )
        return impl

    return register


# ---------------------------------------------------------------- int

@op("int.to_text", ["int"], "text", "write a number as decimal text")
def _(rt, a, f): return str(a[0])


@op("int.parse", ["text"], "int", "read decimal text as a number; fault \"parse\" if it is not one", fallible=True)
def _(rt, a, f):
    s = a[0].strip()
    body = s[1:] if s[:1] == "-" else s
    if not body or not body.isascii() or not body.isdigit():
        raise Fault("parse")
    return chk(int(s))


@op("int.min", ["int", "int"], "int", "smaller of two numbers")
def _(rt, a, f): return min(a)


@op("int.max", ["int", "int"], "int", "larger of two numbers")
def _(rt, a, f): return max(a)


@op("int.abs", ["int"], "int", "absolute value, distance from zero")
def _(rt, a, f): return chk(abs(a[0]))


# ---------------------------------------------------------------- text

@op("text.length", ["text"], "int", "number of characters in text")
def _(rt, a, f): return len(a[0])


@op("text.concat", ["text", "text"], "text", "join two texts end to end")
def _(rt, a, f): return a[0] + a[1]


@op("text.join", ["seq of text", "text"], "text", "join many texts with a separator between them")
def _(rt, a, f): return a[1].join(a[0])


@op("text.split", ["text", "text"], "seq of text", "split text at every occurrence of a non-empty separator")
def _(rt, a, f):
    if a[1] == "":
        raise Trap("argument", "text.split needs a non-empty separator")
    return a[0].split(a[1])


@op("text.split_words", ["text"], "seq of text", "split text into words at whitespace, dropping empty pieces")
def _(rt, a, f): return a[0].split()


@op("text.split_lines", ["text"], "seq of text", "split text into lines")
def _(rt, a, f): return a[0].splitlines()


@op("text.chars", ["text"], "seq of text", "split text into single characters")
def _(rt, a, f): return list(a[0])


@op("text.codes", ["text"], "seq of int", "character code numbers of a text")
def _(rt, a, f): return [ord(c) for c in a[0]]


@op("text.from_codes", ["seq of int"], "text", "build text from character code numbers")
def _(rt, a, f):
    try:
        return "".join(chr(c) for c in a[0])
    except (ValueError, OverflowError):
        raise Trap("argument", "text.from_codes was given a number that is not a character code")


@op("text.upper", ["text"], "text", "convert text to upper case")
def _(rt, a, f): return a[0].upper()


@op("text.lower", ["text"], "text", "convert text to lower case")
def _(rt, a, f): return a[0].lower()


@op("text.trim", ["text"], "text", "remove whitespace from both ends of text")
def _(rt, a, f): return a[0].strip()


@op("text.contains", ["text", "text"], "bool", "does the text contain this piece")
def _(rt, a, f): return a[1] in a[0]


@op("text.starts_with", ["text", "text"], "bool", "does the text begin with this prefix")
def _(rt, a, f): return a[0].startswith(a[1])


@op("text.ends_with", ["text", "text"], "bool", "does the text finish with this suffix")
def _(rt, a, f): return a[0].endswith(a[1])


@op("text.find", ["text", "text"], "maybe of int", "position of the first occurrence of a piece, counting from 0")
def _(rt, a, f):
    i = a[0].find(a[1])
    return some(i) if i >= 0 else NONE


@op("text.take", ["text", "int"], "text", "the first n characters (fewer if the text is shorter)")
def _(rt, a, f): return a[0][: max(a[1], 0)]


@op("text.drop", ["text", "int"], "text", "the text without its first n characters")
def _(rt, a, f): return a[0][max(a[1], 0):]


@op("text.reverse", ["text"], "text", "the characters in opposite order")
def _(rt, a, f): return a[0][::-1]


@op("text.replace", ["text", "text", "text"], "text", "replace every occurrence of a non-empty piece with another")
def _(rt, a, f):
    if a[1] == "":
        raise Trap("argument", "text.replace needs a non-empty piece to look for")
    return a[0].replace(a[1], a[2])


@op("text.repeat", ["text", "int"], "text", "the text written n times in a row")
def _(rt, a, f): return a[0] * max(a[1], 0)


@op("text.is_digits", ["text"], "bool", "is the text non-empty and made only of digits 0-9")
def _(rt, a, f): return a[0].isascii() and a[0].isdigit()


@op("text.is_letters", ["text"], "bool", "is the text non-empty and made only of letters")
def _(rt, a, f): return a[0].isalpha()


# ---------------------------------------------------------------- seq

@op("seq.length", ["seq of A"], "int", "number of items in a sequence")
def _(rt, a, f): return len(a[0])


@op("seq.is_empty", ["seq of A"], "bool", "does the sequence have no items")
def _(rt, a, f): return len(a[0]) == 0


@op("seq.each", ["seq of A"], "seq of B", "apply a unit to every item, giving the sequence of results (map, transform)",
    with_=(["A"], "B"))
def _(rt, a, f): return [f(x) for x in a[0]]


@op("seq.keep", ["seq of A"], "seq of A", "keep only the items for which a unit gives true (filter, select)",
    with_=(["A"], "bool"))
def _(rt, a, f): return [x for x in a[0] if f(x)]


@op("seq.fold", ["seq of A", "B"], "B",
    "combine all items into one value: start from a value, hand the unit (value so far, item) for each item (reduce, accumulate)",
    with_=(["B", "A"], "B"))
def _(rt, a, f):
    acc = a[1]
    for x in a[0]:
        acc = f(acc, x)
    return acc


@op("seq.any", ["seq of A"], "bool", "does a unit give true for at least one item", with_=(["A"], "bool"))
def _(rt, a, f): return any(f(x) for x in a[0])


@op("seq.all", ["seq of A"], "bool", "does a unit give true for every item", with_=(["A"], "bool"))
def _(rt, a, f): return all(f(x) for x in a[0])


@op("seq.count", ["seq of A"], "int", "how many items a unit gives true for", with_=(["A"], "bool"))
def _(rt, a, f): return sum(1 for x in a[0] if f(x))


@op("seq.visit", ["seq of A"], "nothing", "run a unit on every item in order, for its effects only (for each, loop over)",
    with_=(["A"], "nothing"))
def _(rt, a, f):
    for x in a[0]:
        f(x)


@op("seq.sum", ["seq of int"], "int", "add up all the numbers (total)")
def _(rt, a, f):
    total = 0
    for x in a[0]:
        total = chk(total + x)
    return total


@op("seq.max", ["seq of A"], "maybe of A", "largest item, or none for an empty sequence", A="ordered")
def _(rt, a, f): return some(max(a[0])) if a[0] else NONE


@op("seq.min", ["seq of A"], "maybe of A", "smallest item, or none for an empty sequence", A="ordered")
def _(rt, a, f): return some(min(a[0])) if a[0] else NONE


@op("seq.sort", ["seq of A"], "seq of A", "items in increasing order", A="ordered")
def _(rt, a, f): return sorted(a[0])


@op("seq.sort_by", ["seq of A"], "seq of A", "items in increasing order of the key a unit gives for each; ties keep their order",
    with_=(["A"], "K"), K="ordered")
def _(rt, a, f): return sorted(a[0], key=f)


@op("seq.reverse", ["seq of A"], "seq of A", "items in opposite order")
def _(rt, a, f): return a[0][::-1]


@op("seq.first", ["seq of A"], "maybe of A", "the first item, or none for an empty sequence (head)")
def _(rt, a, f): return some(a[0][0]) if a[0] else NONE


@op("seq.last", ["seq of A"], "maybe of A", "the last item, or none for an empty sequence")
def _(rt, a, f): return some(a[0][-1]) if a[0] else NONE


@op("seq.at", ["seq of A", "int"], "maybe of A", "the item at a position counting from 0, or none if out of range (index, get)")
def _(rt, a, f): return some(a[0][a[1]]) if 0 <= a[1] < len(a[0]) else NONE


@op("seq.take", ["seq of A", "int"], "seq of A", "the first n items (fewer if the sequence is shorter)")
def _(rt, a, f): return a[0][: max(a[1], 0)]


@op("seq.drop", ["seq of A", "int"], "seq of A", "the sequence without its first n items (tail, rest)")
def _(rt, a, f): return a[0][max(a[1], 0):]


@op("seq.append", ["seq of A", "A"], "seq of A", "the sequence with one more item at the end (push, add)")
def _(rt, a, f): return a[0] + [a[1]]


@op("seq.concat", ["seq of A", "seq of A"], "seq of A", "two sequences joined end to end")
def _(rt, a, f): return a[0] + a[1]


@op("seq.flatten", ["seq of seq of A"], "seq of A", "join a sequence of sequences into one sequence")
def _(rt, a, f): return [x for xs in a[0] for x in xs]


@op("seq.range", ["int", "int"], "seq of int", "the numbers from a up to but not including b (count, iterate numbers)")
def _(rt, a, f):
    if a[1] - a[0] > 10_000_000:
        raise Trap("argument", "seq.range would create more than 10 million numbers")
    return list(range(a[0], a[1]))


@op("seq.numbered", ["seq of A"], "seq of pair of int and A", "pair every item with its position counting from 0 (enumerate, index)")
def _(rt, a, f): return [pair(i, x) for i, x in enumerate(a[0])]


@op("seq.pair", ["seq of A", "seq of B"], "seq of pair of A and B", "pair up items of two sequences position by position, stopping at the shorter (zip)")
def _(rt, a, f): return [pair(x, y) for x, y in zip(a[0], a[1])]


@op("seq.tally", ["seq of A"], "map of A to int", "count how many times each distinct item occurs (frequency, histogram)")
def _(rt, a, f):
    out = {}
    for x in a[0]:
        out[x] = out.get(x, 0) + 1
    return out


@op("seq.contains", ["seq of A", "A"], "bool", "is this value one of the items (member, includes)")
def _(rt, a, f): return a[1] in a[0]


@op("seq.find", ["seq of A", "A"], "maybe of int", "position of the first item equal to a value, counting from 0")
def _(rt, a, f): return some(a[0].index(a[1])) if a[1] in a[0] else NONE


@op("seq.distinct", ["seq of A"], "seq of A", "the items with repeats removed, keeping first occurrences (unique, dedupe)")
def _(rt, a, f):
    out = []
    for x in a[0]:
        if x not in out:
            out.append(x)
    return out


# ---------------------------------------------------------------- map

@op("map.empty", [], "map of K to V", "a map with no entries")
def _(rt, a, f): return {}


@op("map.get", ["map of K to V", "K"], "maybe of V", "the value stored under a key, or none (lookup)")
def _(rt, a, f): return some(a[0][a[1]]) if a[1] in a[0] else NONE


@op("map.get_or", ["map of K to V", "K", "V"], "V", "the value stored under a key, or a fallback value if the key is absent")
def _(rt, a, f): return a[0].get(a[1], a[2])


@op("map.put", ["map of K to V", "K", "V"], "map of K to V", "the map with a key set to a value (insert, update, set)")
def _(rt, a, f): return {**a[0], a[1]: a[2]}


@op("map.remove", ["map of K to V", "K"], "map of K to V", "the map without a key (delete)")
def _(rt, a, f): return {k: v for k, v in a[0].items() if k != a[1]}


@op("map.has", ["map of K to V", "K"], "bool", "is there an entry under this key")
def _(rt, a, f): return a[1] in a[0]


@op("map.size", ["map of K to V"], "int", "number of entries in a map")
def _(rt, a, f): return len(a[0])


@op("map.keys", ["map of K to V"], "seq of K", "all keys, in the order they were first added")
def _(rt, a, f): return list(a[0].keys())


@op("map.values", ["map of K to V"], "seq of V", "all values, in the order their keys were first added")
def _(rt, a, f): return list(a[0].values())


@op("map.entries", ["map of K to V"], "seq of pair of K and V", "all (key, value) pairs, in the order keys were first added (items)")
def _(rt, a, f): return [pair(k, v) for k, v in a[0].items()]


@op("map.from_entries", ["seq of pair of K and V"], "map of K to V", "build a map from (key, value) pairs; later pairs win")
def _(rt, a, f): return {p["first"]: p["second"] for p in a[0]}


# ---------------------------------------------------------------- maybe, pair, loop

@op("maybe.some", ["A"], "maybe of A", "a present value")
def _(rt, a, f): return some(a[0])


@op("maybe.none", [], "maybe of A", "an absent value")
def _(rt, a, f): return NONE


@op("maybe.or", ["maybe of A", "A"], "A", "the present value, or a fallback if absent (default, unwrap)")
def _(rt, a, f): return a[0][1] if a[0][0] == "some" else a[1]


@op("maybe.is_some", ["maybe of A"], "bool", "is a value present")
def _(rt, a, f): return a[0][0] == "some"


@op("pair.make", ["A", "B"], "pair of A and B", "a pair of two values, read back with .first and .second (tuple)")
def _(rt, a, f): return pair(a[0], a[1])


@op("loop.again", ["S"], "loop of S and R", "tell `repeat` to go round again with a new state")
def _(rt, a, f): return ("again", a[0])


@op("loop.done", ["R"], "loop of S and R", "tell `repeat` to stop with a final result")
def _(rt, a, f): return ("done", a[0])


# ---------------------------------------------------------------- effects

@op("console.write", ["text"], "nothing", "print a line of text to the screen (output)", effect="console write")
def _(rt, a, f): return rt.effect("console.write", a, lambda: rt.console_write(a[0]))


@op("console.read_line", [], "text", "read one line typed by the user; fault \"end\" when input is finished (input)",
    fallible=True, effect="console read")
def _(rt, a, f): return rt.effect("console.read_line", a, rt.console_read_line)


@op("files.read_text", ["text"], "text", "read a whole file as text; fault \"missing\", \"denied\" or \"io\"",
    fallible=True, effect="files read")
def _(rt, a, f): return rt.effect("files.read_text", a, lambda: rt.file_read(a[0]))


@op("files.write_text", ["text", "text"], "nothing", "replace a file's contents with text; fault \"denied\" or \"io\"",
    fallible=True, effect="files write")
def _(rt, a, f): return rt.effect("files.write_text", a, lambda: rt.file_write(a[0], a[1]))


@op("files.exists", ["text"], "bool", "is there a file or folder at this path", effect="files read")
def _(rt, a, f): return rt.effect("files.exists", a, lambda: rt.file_exists(a[0]))


@op("files.list", ["text"], "seq of text", "names inside a folder, sorted; fault \"missing\", \"denied\" or \"io\"",
    fallible=True, effect="files read")
def _(rt, a, f): return rt.effect("files.list", a, lambda: rt.file_list(a[0]))


@op("clock.now_ms", [], "int", "current time in milliseconds since 1970", effect="clock")
def _(rt, a, f): return rt.effect("clock.now_ms", a, rt.clock_now_ms)


@op("random.int", ["int", "int"], "int", "a random number from lo up to and including hi", effect="random")
def _(rt, a, f):
    if a[0] > a[1]:
        raise Trap("argument", "random.int needs lo <= hi")
    return rt.effect("random.int", a, lambda: rt.random_int(a[0], a[1]))


NAMESPACES = sorted({name.split(".")[0] for name in BUILTINS})
EFFECTS = sorted({b.effect for b in BUILTINS.values() if b.effect})
BUILTIN_CHOICES = {
    "maybe": [("some", 0), ("none", None)],   # case -> index of the type argument it carries
    "loop": [("again", 0), ("done", 1)],
}
