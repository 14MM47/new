"""Type representation, parsing, printing and unification.

Types are plain tuples so they hash and compare structurally:
    ("int",) ("bool",) ("text",) ("nothing",)
    ("seq", T) ("maybe", T) ("map", K, V) ("pair", A, B) ("loop", S, R)
    ("named", name)          a record or choice declared in the program
    ("var", key)             an unknown still to be inferred
"""
from __future__ import annotations

import itertools

INT = ("int",)
BOOL = ("bool",)
TEXT = ("text",)
NOTHING = ("nothing",)

SIMPLE = {"int": INT, "bool": BOOL, "text": TEXT, "nothing": NOTHING}
# generic type -> separator words between its arguments
GENERIC = {"seq": [], "maybe": [], "map": ["to"], "pair": ["and"], "loop": ["and"]}

_fresh = itertools.count()


class TypeError_(Exception):
    pass


def fresh() -> tuple:
    return ("var", next(_fresh))


def show(t: tuple) -> str:
    k = t[0]
    if k in SIMPLE:
        return k
    if k == "named":
        return t[1]
    if k == "var":
        return t[1] if isinstance(t[1], str) else "?"
    seps = GENERIC[k]
    parts = [show(t[1])]
    for sep, arg in zip(seps, t[2:]):
        parts += [sep, show(arg)]
    return f"{k} of " + " ".join(parts)


def parse(tokens: list, pos: int, named: set, vars_ok: bool = False):
    """Parse a type starting at tokens[pos]. Returns (type, new_pos)."""
    if pos >= len(tokens):
        raise TypeError_("a type is missing")
    kind, val = tokens[pos]
    if kind != "name":
        raise TypeError_(f"`{val}` is not a type")
    if val in SIMPLE:
        return SIMPLE[val], pos + 1
    if val in GENERIC:
        if pos + 1 >= len(tokens) or tokens[pos + 1][1] != "of":
            raise TypeError_(f"`{val}` must be followed by `of`")
        args = []
        arg, pos = parse(tokens, pos + 2, named, vars_ok)
        args.append(arg)
        for sep in GENERIC[val]:
            if pos >= len(tokens) or tokens[pos][1] != sep:
                raise TypeError_(f"`{val} of` needs `{sep}` before its next type")
            arg, pos = parse(tokens, pos + 1, named, vars_ok)
            args.append(arg)
        return (val, *args), pos
    if vars_ok and len(val) == 1 and val.isupper():
        return ("var", val), pos + 1
    if val in named:
        return ("named", val), pos + 1
    raise TypeError_(f"`{val}` is not a known type")


def resolve(t: tuple, subst: dict) -> tuple:
    if t[0] == "var":
        while t[0] == "var" and t in subst:
            t = subst[t]
        if t[0] == "var":
            return t
    if len(t) == 1 or t[0] == "named":
        return t
    return (t[0], *(resolve(a, subst) for a in t[1:]))


def unify(a: tuple, b: tuple, subst: dict) -> bool:
    a, b = resolve(a, subst), resolve(b, subst)
    if a == b:
        return True
    if a[0] == "var":
        subst[a] = b
        return True
    if b[0] == "var":
        subst[b] = a
        return True
    if a[0] != b[0] or len(a) != len(b) or a[0] == "named":
        return False
    return all(unify(x, y, subst) for x, y in zip(a[1:], b[1:]))


def has_var(t: tuple) -> bool:
    if t[0] == "var":
        return True
    if t[0] == "named":
        return False
    return any(has_var(a) for a in t[1:])


def instantiate(t: tuple, mapping: dict) -> tuple:
    """Replace signature variables ("var", "A") with fresh unknowns."""
    if t[0] == "var":
        if t not in mapping:
            mapping[t] = fresh()
        return mapping[t]
    if len(t) == 1 or t[0] == "named":
        return t
    return (t[0], *(instantiate(a, mapping) for a in t[1:]))
