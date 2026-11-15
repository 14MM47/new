"""Cards and search.

A card is the machine-derived summary of a definition: everything needed to use
it without reading its steps. `find` answers "what is there that does X?", so an
author asks instead of guessing a name.
"""
from __future__ import annotations

import re

from . import types as T
from .builtins import BUILTINS


def unit_card(u) -> str:
    lines = [f"unit {u.name}  #{u.hash}", f'  why "{u.why}"']
    lines += [f"  takes {n} : {T.show(t)}" for n, t in u.params]
    lines.append(f"  gives {T.show(u.ret)}" + (" or fault" if u.fallible else ""))
    if u.needs:
        lines.append("  needs " + ", ".join(sorted(u.needs)))
    if u.shrinks:
        lines.append(f"  shrinks {u.shrinks}")
    lines += [f"  require {src}" for _, src in u.requires]
    lines += [f"  ensure {src}" for _, src in u.ensures]
    lines.append(f"  evidence {u.evidence}")
    return "\n".join(lines)


def record_card(r) -> str:
    return "\n".join([f"record {r.name}  #{r.hash}"] + [f"  {n} : {T.show(t)}" for n, t in r.fields])


def choice_card(c) -> str:
    return "\n".join([f"choice {c.name}  #{c.hash}"]
                     + [f"  {n}" + (f" : {T.show(t)}" if t is not None else "") for n, t in c.cases])


def card(prog, name: str):
    if name in prog.units:
        return unit_card(prog.units[name])
    if name in prog.records:
        return record_card(prog.records[name])
    if name in prog.choices:
        return choice_card(prog.choices[name])
    if name in BUILTINS:
        b = BUILTINS[name]
        extra = f"\n  needs {b.effect}" if b.effect else ""
        return f"{b.signature()}\n  {b.doc}{extra}"
    return None


def _words(text: str) -> set:
    out = set()
    for w in re.findall(r"[a-z0-9]+", text.lower()):
        out.add(w)
        if len(w) > 3 and w.endswith("s"):
            out.add(w[:-1])
        if len(w) > 4 and w.endswith("ing"):
            out.add(w[:-3])
    return out


_STOP = {"a", "an", "the", "of", "to", "in", "is", "it", "for", "and", "or", "with", "by", "on", "at", "from", "each", "every"}


def find(prog, query: str, limit: int = 6) -> list:
    """Return up to `limit` (score, one-line description) matches for a plain-words query."""
    want = _words(query) - _STOP
    scored = []
    for name, b in BUILTINS.items():
        have_name = _words(name.replace(".", " ").replace("_", " "))
        have_doc = _words(b.doc)
        score = 3 * len(want & have_name) + len(want & have_doc)
        if score:
            scored.append((score, f"{b.signature()}  -- {b.doc}"))
    if prog is not None:
        for u in prog.units.values():
            have_name = _words(u.name.replace("_", " "))
            score = 3 * len(want & have_name) + len(want & _words(u.why or ""))
            if score:
                ins = " ".join(f"({T.show(t)})" if " " in T.show(t) else T.show(t) for _, t in u.params)
                tail = " or fault" if u.fallible else ""
                scored.append((score, f"{u.name} {ins} -> {T.show(u.ret)}{tail}  -- {u.why}"))
    scored.sort(key=lambda s: -s[0])
    return scored[:limit]
